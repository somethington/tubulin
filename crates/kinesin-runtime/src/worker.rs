//! Running one job — either via a fork-server child (over a Unix socket) or by
//! spawning a disposable `python -m kinesin.worker` process. Both stream the same
//! line protocol and are funneled through `on_msg`.

use std::path::Path;
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use kinesin_protocol::{JobDef, WorkerMessage, WorkerResult};
use serde_json::Value;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::process::Command;

use crate::config::Config;
use crate::forkserver::ForkServerManager;

/// Run one job to completion, invoking `on_msg` for every line the worker emits.
///
/// Prefers a fork-server child (no per-job import) when one is available for the
/// job's module; otherwise spawns a fresh worker process. Either way exactly one
/// terminal message (`Completed`/`Failed`) is delivered — a synthetic `Failed` is
/// produced if the worker dies without one.
pub async fn run_worker<F>(
    config: &Config,
    forkservers: &ForkServerManager,
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

    // Fast path: route to the module's fork-server if one is (or can be) running.
    if let Some(socket) = forkservers.socket_for(&job_def.module_file).await {
        if run_via_socket(&socket, config, &descriptor, &mut on_msg).await {
            return;
        }
        // Couldn't connect (server died?) — fall through to a plain worker.
    }
    run_via_process(config, &descriptor, &mut on_msg).await;
}

/// Read worker protocol lines off `reader`, dispatching each to `on_msg`. Returns
/// whether a terminal (`Completed`/`Failed`) message was seen.
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
                if matches!(
                    msg,
                    WorkerMessage::Completed { .. } | WorkerMessage::Failed { .. }
                ) {
                    saw_terminal = true;
                }
                on_msg(msg);
            }
            // Not protocol — treat as a log line so nothing is silently lost.
            Err(_) => on_msg(WorkerMessage::Log {
                message: line.to_string(),
            }),
        }
    }
    saw_terminal
}

/// Route a job to a fork-server over its Unix socket. Returns `false` if the socket
/// couldn't be connected (so the caller can fall back to a process).
async fn run_via_socket<F>(
    socket: &Path,
    config: &Config,
    descriptor: &Value,
    on_msg: &mut F,
) -> bool
where
    F: FnMut(WorkerMessage),
{
    let stream = match UnixStream::connect(socket).await {
        Ok(s) => s,
        Err(_) => return false,
    };
    let (read_half, mut write_half) = stream.into_split();

    // Send the descriptor as one line, then half-close so the child sees EOF.
    let mut payload = descriptor.to_string();
    payload.push('\n');
    if write_half.write_all(payload.as_bytes()).await.is_err() {
        return false;
    }
    let _ = write_half.shutdown().await;

    let timeout = Duration::from_secs(config.max_runtime_seconds);
    let reader = BufReader::new(read_half);
    match tokio::time::timeout(timeout, pump(reader, on_msg)).await {
        Err(_) => {
            // Dropping the socket sends the child EPIPE on its next emit.
            on_msg(WorkerMessage::Failed {
                error: format!("job exceeded max_runtime_seconds ({}s)", timeout.as_secs()),
            });
        }
        Ok(false) => {
            // The forked child closed the connection without a terminal message —
            // it likely crashed (e.g. a fork-unsafe native call).
            on_msg(WorkerMessage::Failed {
                error: "fork-server child exited without a result (possible crash)".into(),
            });
        }
        Ok(true) => {}
    }
    true
}

/// Spawn a fresh `python -m kinesin.worker`, feed it the descriptor, stream its output.
async fn run_via_process<F>(config: &Config, descriptor: &Value, on_msg: &mut F)
where
    F: FnMut(WorkerMessage),
{
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
    let reader = BufReader::new(stdout);
    let timeout = Duration::from_secs(config.max_runtime_seconds);

    let saw_terminal = match tokio::time::timeout(timeout, pump(reader, on_msg)).await {
        Ok(saw) => saw,
        Err(_) => {
            let _ = child.start_kill();
            on_msg(WorkerMessage::Failed {
                error: format!("job exceeded max_runtime_seconds ({}s)", timeout.as_secs()),
            });
            let _ = child.wait().await;
            return;
        }
    };

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
