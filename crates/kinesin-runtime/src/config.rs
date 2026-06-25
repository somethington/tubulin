//! Runtime configuration: `kinesin.toml` merged with defaults, plus paths and the
//! Python interpreter resolved relative to the project root.

use std::path::{Path, PathBuf};

use serde::Deserialize;

#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    pub max_workers: usize,
    pub max_runtime_seconds: u64,
    pub max_queue_size: usize,
    pub project_root: PathBuf,
    pub upload_dir: PathBuf,
    pub result_dir: PathBuf,
    pub cache_dir: PathBuf,
    pub frontend_dir: PathBuf,
    /// Python interpreter used for registration, workers and previews.
    pub python: String,
    /// Value for the workers' `PYTHONPATH` so `import kinesin` resolves.
    pub pythonpath: PathBuf,
}

// ---- Raw (on-disk) shape with defaults ------------------------------------

#[derive(Debug, Default, Deserialize)]
struct RawConfig {
    #[serde(default)]
    server: RawServer,
    #[serde(default)]
    workers: RawWorkers,
    #[serde(default)]
    jobs: RawJobs,
    #[serde(default)]
    storage: RawStorage,
}

#[derive(Debug, Deserialize)]
struct RawServer {
    host: String,
    port: u16,
}
impl Default for RawServer {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".into(),
            port: 8000,
        }
    }
}

#[derive(Debug, Deserialize)]
struct RawWorkers {
    max_workers: usize,
}
impl Default for RawWorkers {
    fn default() -> Self {
        Self { max_workers: 4 }
    }
}

#[derive(Debug, Deserialize)]
struct RawJobs {
    max_runtime_seconds: u64,
    max_queue_size: usize,
}
impl Default for RawJobs {
    fn default() -> Self {
        Self {
            max_runtime_seconds: 3600,
            max_queue_size: 100,
        }
    }
}

#[derive(Debug, Deserialize)]
struct RawStorage {
    upload_dir: String,
    result_dir: String,
    cache_dir: String,
}
impl Default for RawStorage {
    fn default() -> Self {
        Self {
            upload_dir: "storage/uploads".into(),
            result_dir: "storage/results".into(),
            cache_dir: "storage/cache".into(),
        }
    }
}

impl Config {
    /// Load config, applying a CLI port override if present.
    pub fn load(config_path: Option<PathBuf>, port_override: Option<u16>) -> anyhow::Result<Self> {
        let project_root = match std::env::var("KINESIN_ROOT") {
            Ok(p) => PathBuf::from(p),
            Err(_) => std::env::current_dir()?,
        };

        let config_file = config_path.unwrap_or_else(|| project_root.join("kinesin.toml"));
        let raw: RawConfig = if config_file.exists() {
            let text = std::fs::read_to_string(&config_file)?;
            toml::from_str(&text)?
        } else {
            RawConfig::default()
        };

        let resolve = |p: &str| -> PathBuf {
            let path = Path::new(p);
            if path.is_absolute() {
                path.to_path_buf()
            } else {
                project_root.join(path)
            }
        };

        let python = std::env::var("KINESIN_PYTHON").unwrap_or_else(|_| {
            let venv = project_root.join(".venv/bin/python");
            if venv.exists() {
                venv.to_string_lossy().into_owned()
            } else {
                "python3".to_string()
            }
        });

        let frontend_dir = std::env::var("KINESIN_FRONTEND")
            .map(PathBuf::from)
            .unwrap_or_else(|_| project_root.join("frontend/dist"));

        // Port precedence: CLI --port > PORT env (cloud/preview convention) > toml.
        let env_port = std::env::var("PORT").ok().and_then(|p| p.parse::<u16>().ok());

        // max_workers = 0 means "auto": use the machine's cores. This sizes the
        // worker pool and bounds how many shards a parallel job fans out into.
        let max_workers = if raw.workers.max_workers == 0 {
            std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(4)
        } else {
            raw.workers.max_workers
        };

        Ok(Config {
            host: raw.server.host,
            port: port_override.or(env_port).unwrap_or(raw.server.port),
            max_workers,
            max_runtime_seconds: raw.jobs.max_runtime_seconds,
            max_queue_size: raw.jobs.max_queue_size.max(1),
            upload_dir: resolve(&raw.storage.upload_dir),
            result_dir: resolve(&raw.storage.result_dir),
            cache_dir: resolve(&raw.storage.cache_dir),
            frontend_dir,
            python,
            pythonpath: project_root.join("python"),
            project_root,
        })
    }

    /// Create the storage directories if they don't already exist.
    pub fn ensure_dirs(&self) -> anyhow::Result<()> {
        for dir in [&self.upload_dir, &self.result_dir, &self.cache_dir] {
            std::fs::create_dir_all(dir)?;
        }
        Ok(())
    }
}
