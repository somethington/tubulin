mod raw;

use std::path::{Path, PathBuf};

use anyhow::Context;

use raw::RawConfig;

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
    /// Python interpreter used for registration, workers, and previews.
    pub python: String,
    /// Workers' `PYTHONPATH` so `import kinesin` resolves.
    pub pythonpath: PathBuf,
}

impl Config {
    /// Load config, applying a CLI port override if present.
    pub fn load(config_path: Option<PathBuf>, port_override: Option<u16>) -> anyhow::Result<Self> {
        let project_root = match std::env::var("KINESIN_ROOT") {
            Ok(p) => PathBuf::from(p),
            Err(_) => std::env::current_dir()?,
        };
        // A canonical root keeps path-containment checks correct under symlinks.
        let project_root = std::fs::canonicalize(&project_root)
            .with_context(|| format!("cannot resolve project root {}", project_root.display()))?;

        let config_file = config_path.unwrap_or_else(|| project_root.join("kinesin.toml"));
        let raw: RawConfig = if config_file.exists() {
            toml::from_str(&std::fs::read_to_string(&config_file)?)?
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

        // Port precedence: CLI --port > PORT env > kinesin.toml.
        let env_port = std::env::var("PORT")
            .ok()
            .and_then(|p| p.parse::<u16>().ok());

        Ok(Config {
            host: raw.server.host,
            port: port_override.or(env_port).unwrap_or(raw.server.port),
            max_workers: effective_workers(raw.workers.max_workers),
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

/// `max_workers = 0` means auto: size the worker pool to the machine's cores.
fn effective_workers(configured: usize) -> usize {
    if configured == 0 {
        std::thread::available_parallelism().map_or(4, |n| n.get())
    } else {
        configured
    }
}
