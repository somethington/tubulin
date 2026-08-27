use std::collections::hash_map::DefaultHasher;
use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Child;
use tokio::sync::Mutex;

use crate::config::Config;

const READY: &str = "TUBULIN_FORKSERVER_READY";

struct ForkServer {
    /// Held so the process is killed on drop (`kill_on_drop`); never read otherwise.
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
        let enabled = match std::env::var("TUBULIN_FORKSERVER").ok().as_deref() {
            Some("1") => true,
            Some("0") => false,
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

    /// Drop a dead server so the next job restarts it instead of failing forever.
    pub async fn evict(&self, module_file: &str) {
        if self.servers.lock().await.remove(module_file).is_some() {
            tracing::warn!(module = %module_file, "fork-server unreachable; evicted for restart");
        }
    }

    async fn start(&self, module_file: &str) -> Option<ForkServer> {
        let mut hasher = DefaultHasher::new();
        module_file.hash(&mut hasher);
        let socket = self
            .config
            .result_dir
            .join(format!("forkserver-{:x}.sock", hasher.finish()));
        let _ = std::fs::remove_file(&socket);

        let mut child = self
            .config
            .python_command()
            .arg("-m")
            .arg("tubulin.forkserver")
            .arg(module_file)
            .arg(&socket)
            .env("TUBULIN_WORKER", "1")
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
