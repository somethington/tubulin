//! Spawning a disposable Python worker and streaming its line protocol.

use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use kinesin_protocol::{JobDef, WorkerMessage, WorkerResult};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

use crate::config::Config;

/// Run one job to completion, invoking `on_msg` for every line the worker emits.
///
/// Guarantees that exactly one terminal message (`Completed` or `Failed`) is
/// delivered: if the worker dies without emitting one, a `Failed` synthesised from
/// its stderr / exit status is sent.
pub async fn run_worker<F>(
    config: &Config,
    job_id: &str,
    job_def: &JobDef,
    args: &[Value],
    shard: usize,
    num_shards: usize,
    mut on_msg: F,
) where
    F: FnMut(WorkerMessage),
{
    let descriptor = serde_json::json!({
        "job_id": job_id,
        "module_file": job_def.module_file,
        "qualname": job_def.qualname,
        "args": args,
        "result_dir": config.result_dir.to_string_lossy(),
        "shard": shard,
        "num_shards": num_shards,
    });

    let mut child = match Command::new(&config.python)
        .arg("-m")
        .arg("kinesin.worker")
        .env("PYTHONPATH", &config.pythonpath)
        .env("KINESIN_WORKER", "1")
        .current_dir(&config.project_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
    {
        Ok(child) => child,
        Err(e) => {
            on_msg(WorkerMessage::Failed {
                error: format!("failed to spawn worker ({}): {e}", config.python),
            });
            return;
        }
    };

    // Feed the descriptor and close stdin so the worker sees EOF.
    if let Some(mut stdin) = child.stdin.take() {
        let _ = stdin.write_all(descriptor.to_string().as_bytes()).await;
        let _ = stdin.shutdown().await;
    }

    // Drain stderr concurrently so a chatty worker can't deadlock on a full pipe.
    let stderr_buf = Arc::new(Mutex::new(String::new()));
    if let Some(mut stderr) = child.stderr.take() {
        let buf = stderr_buf.clone();
        tokio::spawn(async move {
            let mut s = String::new();
            let _ = stderr.read_to_string(&mut s).await;
            *buf.lock().unwrap() = s;
        });
    }

    let stdout = child.stdout.take().expect("piped stdout");
    let mut lines = BufReader::new(stdout).lines();
    let mut saw_terminal = false;

    let timeout = Duration::from_secs(config.max_runtime_seconds);
    let read_loop = async {
        while let Ok(Some(line)) = lines.next_line().await {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<WorkerMessage>(line) {
                Ok(msg) => {
                    if matches!(
                        msg,
                        WorkerMessage::Completed { .. } | WorkerMessage::Failed { .. }
                    ) {
                        saw_terminal = true;
                    }
                    on_msg(msg);
                }
                Err(_) => {
                    // Not protocol — treat as a log line so nothing is silently lost.
                    on_msg(WorkerMessage::Log {
                        message: line.to_string(),
                    });
                }
            }
        }
    };

    let timed_out = tokio::time::timeout(timeout, read_loop).await.is_err();
    if timed_out {
        let _ = child.start_kill();
        on_msg(WorkerMessage::Failed {
            error: format!("job exceeded max_runtime_seconds ({}s)", timeout.as_secs()),
        });
        let _ = child.wait().await;
        return;
    }

    let status = child.wait().await;

    if !saw_terminal {
        let stderr = stderr_buf.lock().unwrap().clone();
        let code = status.ok().and_then(|s| s.code());
        if stderr.trim().is_empty() {
            on_msg(WorkerMessage::Completed {
                result: WorkerResult {
                    path: None,
                    outputs: Default::default(),
                },
            });
        } else {
            on_msg(WorkerMessage::Failed {
                error: format!(
                    "worker exited (code {:?}) without a result:\n{}",
                    code,
                    stderr.trim()
                ),
            });
        }
    }
}
