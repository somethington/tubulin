use std::process::Stdio;
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncReadExt, AsyncWriteExt, BufReader};
use tubulin_protocol::{WorkerMessage, WorkerResult};

use crate::config::Config;

use super::pump;

pub(super) async fn run<F>(config: &Config, descriptor: &Value, on_msg: &mut F)
where
    F: FnMut(WorkerMessage),
{
    let mut child = match config
        .python_command()
        .arg("-m")
        .arg("tubulin.worker")
        .env("TUBULIN_WORKER", "1")
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
    let stderr_task = child.stderr.take().map(|mut stderr| {
        tokio::spawn(async move {
            let mut buf = String::new();
            let _ = stderr.read_to_string(&mut buf).await;
            buf
        })
    });

    let stdout = child.stdout.take().expect("piped stdout");
    let timeout = Duration::from_secs(config.max_runtime_seconds);

    let saw_terminal =
        match tokio::time::timeout(timeout, pump(BufReader::new(stdout), on_msg)).await {
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
    if saw_terminal {
        return;
    }

    // No terminal message: await the full stderr before judging how the worker died.
    let stderr = match stderr_task {
        Some(task) => task.await.unwrap_or_default(),
        None => String::new(),
    };
    let stderr = stderr.trim();
    let clean_exit = status.as_ref().is_ok_and(|s| s.success());

    if clean_exit && stderr.is_empty() {
        // A quiet, successful worker that produced nothing counts as an empty result.
        on_msg(WorkerMessage::Completed {
            result: WorkerResult {
                path: None,
                outputs: Default::default(),
            },
        });
    } else {
        let code = status.ok().and_then(|s| s.code());
        let mut error = format!("worker exited (code {code:?}) without a result");
        if !stderr.is_empty() {
            error.push_str(":\n");
            error.push_str(stderr);
        }
        on_msg(WorkerMessage::Failed { error });
    }
}
