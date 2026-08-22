use std::path::Path;
use std::time::Duration;

use serde_json::Value;
use tokio::io::{AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tubulin_protocol::WorkerMessage;

use crate::config::Config;

use super::pump;

/// Returns `false` when the socket is unreachable so the caller can fall back.
pub(super) async fn run<F>(
    socket: &Path,
    config: &Config,
    descriptor: &Value,
    on_msg: &mut F,
) -> bool
where
    F: FnMut(WorkerMessage),
{
    let Ok(stream) = UnixStream::connect(socket).await else {
        return false;
    };
    let (read_half, mut write_half) = stream.into_split();

    // One descriptor line, then half-close so the child sees EOF.
    let mut payload = descriptor.to_string();
    payload.push('\n');
    if write_half.write_all(payload.as_bytes()).await.is_err() {
        return false;
    }
    let _ = write_half.shutdown().await;

    let timeout = Duration::from_secs(config.max_runtime_seconds);
    let reader = BufReader::new(read_half);
    match tokio::time::timeout(timeout, pump(reader, on_msg)).await {
        // Dropping the socket sends the child EPIPE on its next emit.
        Err(_) => on_msg(WorkerMessage::Failed {
            error: format!("job exceeded max_runtime_seconds ({}s)", timeout.as_secs()),
        }),
        // A close without a terminal message means the forked child crashed.
        Ok(false) => on_msg(WorkerMessage::Failed {
            error: "fork-server child exited without a result (possible crash)".into(),
        }),
        Ok(true) => {}
    }
    true
}
