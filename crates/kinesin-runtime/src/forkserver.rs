//! Per-module Python fork-servers — amortize the heavy worker import.
//!
//! Instead of spawning `python -m kinesin.worker` (cold import every time) for each
//! job/shard, the runtime keeps one `python -m kinesin.forkserver <module>` alive per
//! module file. It imports that module once and `fork()`s a fresh, disposable child
//! per job. The runtime routes a job by connecting to the server's Unix socket,
//! sending the descriptor, and reading the same worker line-protocol back.
//!
//! Gated to platforms where fork-without-exec is safe (Linux by default; on macOS
//! Apple's Accelerate framework segfaults a forked child). Override with the
//! `KINESIN_FORKSERVER` env var (`1` = force on, `0` = force off). When disabled or a
//! server fails to start, the caller falls back to the plain per-job worker.

use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::Mutex;

use crate::config::Config;

const READY: &str = "KINESIN_FORKSERVER_READY";

struct ForkServer {
    // Held so the process is killed on drop (kill_on_drop). Never read otherwise.
    _child: Child,
    socket: PathBuf,
}

pub struct ForkServerManager {
    config: Arc<Config>,
    enabled: bool,
    servers: Mutex<HashMap<String, ForkServer>>,
}

impl ForkServerManager {
    pub fn new(config: Arc<Config>) -> Self {
        let enabled = match std::env::var("KINESIN_FORKSERVER").ok().as_deref() {
            Some("1") => true,
            Some("0") => false,
            // fork-without-exec is safe on Linux (OpenBLAS); not on macOS (Accelerate).
            _ => cfg!(target_os = "linux"),
        };
        if enabled {
            tracing::info!("fork-server enabled (amortizing per-job imports)");
        }
        Self {
            config,
            enabled,
            servers: Mutex::new(HashMap::new()),
        }
    }

    /// Path to `module_file`'s fork-server socket, starting the server on first use.
    /// Returns `None` (caller falls back to a plain worker) when disabled or on failure.
    pub async fn socket_for(&self, module_file: &str) -> Option<PathBuf> {
        if !self.enabled {
            return None;
        }
        let mut servers = self.servers.lock().await;
        if let Some(server) = servers.get(module_file) {
            return Some(server.socket.clone());
        }
        let server = self.start(module_file).await?;
        let socket = server.socket.clone();
        servers.insert(module_file.to_string(), server);
        Some(socket)
    }

    async fn start(&self, module_file: &str) -> Option<ForkServer> {
        let mut hasher = DefaultHasher::new();
        module_file.hash(&mut hasher);
        let socket = self
            .config
            .result_dir
            .join(format!("forkserver-{:x}.sock", hasher.finish()));
        let _ = std::fs::remove_file(&socket);

        let mut child = Command::new(&self.config.python)
            .arg("-m")
            .arg("kinesin.forkserver")
            .arg(module_file)
            .arg(&socket)
            .env("PYTHONPATH", &self.config.pythonpath)
            .env("KINESIN_WORKER", "1")
            .current_dir(&self.config.project_root)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .spawn()
            .ok()?;

        // Wait for the readiness line (import done + socket accepting).
        let stdout = child.stdout.take()?;
        let mut lines = BufReader::new(stdout).lines();
        let ready = tokio::time::timeout(Duration::from_secs(60), async {
            while let Ok(Some(line)) = lines.next_line().await {
                if line.contains(READY) {
                    return true;
                }
            }
            false
        })
        .await
        .unwrap_or(false);

        if !ready {
            tracing::warn!(module = %module_file, "fork-server failed to start; falling back to per-job workers");
            let _ = child.start_kill();
            return None;
        }

        // Keep draining stdout so the server never blocks on a full pipe.
        tokio::spawn(async move { while let Ok(Some(_)) = lines.next_line().await {} });
        tracing::info!(module = %module_file, "fork-server ready");
        Some(ForkServer {
            _child: child,
            socket,
        })
    }
}
