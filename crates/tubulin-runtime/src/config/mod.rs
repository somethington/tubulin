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
    /// `PYTHONPATH` for spawned Python, so `import tubulin` resolves from a source
    /// checkout. `None` when tubulin is installed — the package is already importable
    /// from site-packages, and prepending a stray path could shadow it.
    pub pythonpath: Option<PathBuf>,
}

impl Config {
    /// Load config, applying a CLI port override if present.
    pub fn load(config_path: Option<PathBuf>, port_override: Option<u16>) -> anyhow::Result<Self> {
        let project_root = match std::env::var("TUBULIN_ROOT") {
            Ok(p) => PathBuf::from(p),
            Err(_) => std::env::current_dir()?,
        };
        // A canonical root keeps path-containment checks correct under symlinks.
        let project_root = std::fs::canonicalize(&project_root)
            .with_context(|| format!("cannot resolve project root {}", project_root.display()))?;

        let config_file = config_path.unwrap_or_else(|| project_root.join("tubulin.toml"));
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

        let python = resolve_python(&project_root);

        // A source checkout imports tubulin from `python/`; an installed one doesn't
        // need PYTHONPATH at all.
        let pythonpath = Some(project_root.join("python")).filter(|p| p.join("tubulin").is_dir());

        let frontend_dir = resolve_frontend(&project_root, &python);

        // Port precedence: CLI --port > PORT env > tubulin.toml.
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
            pythonpath,
            project_root,
        })
    }

    /// A `python` invocation with this config's interpreter and import path applied.
    /// Every Python spawn in the runtime goes through here so the two stay in step.
    pub fn python_command(&self) -> tokio::process::Command {
        let mut cmd = tokio::process::Command::new(&self.python);
        if let Some(path) = &self.pythonpath {
            cmd.env("PYTHONPATH", path);
        }
        cmd
    }

    /// Whether we're running from a source checkout rather than an installed wheel.
    /// `pythonpath` is the discriminator: it is set only when `python/tubulin/` sits
    /// next to us. The two shapes need different advice when something is missing.
    pub fn is_source_checkout(&self) -> bool {
        self.pythonpath.is_some()
    }

    /// Create the storage directories if they don't already exist.
    pub fn ensure_dirs(&self) -> anyhow::Result<()> {
        for dir in [&self.upload_dir, &self.result_dir, &self.cache_dir] {
            std::fs::create_dir_all(dir)?;
        }
        Ok(())
    }
}

/// Which interpreter to spawn.
///
/// The installed case is the interesting one: `pip install tubulin` puts the `tubulin`
/// binary in the environment's `bin/` alongside its `python`, so the interpreter that
/// has tubulin importable is the one sitting next to this executable. Preferring it
/// over bare `python3` is what makes an installed tubulin work inside a venv without
/// the venv being activated.
fn resolve_python(project_root: &Path) -> String {
    if let Ok(explicit) = std::env::var("TUBULIN_PYTHON") {
        return explicit;
    }
    if let Some(sibling) = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join(PYTHON_EXE)))
        .filter(|candidate| candidate.exists())
    {
        return sibling.to_string_lossy().into_owned();
    }
    let venv = project_root.join(VENV_PYTHON);
    if venv.exists() {
        return venv.to_string_lossy().into_owned();
    }
    "python3".to_string()
}

/// Where the built frontend lives: an explicit override, a source checkout's
/// `frontend/dist`, or the copy packaged inside the installed `tubulin` package.
fn resolve_frontend(project_root: &Path, python: &str) -> PathBuf {
    if let Ok(explicit) = std::env::var("TUBULIN_FRONTEND") {
        return PathBuf::from(explicit);
    }
    let in_tree = project_root.join("frontend/dist");
    if in_tree.exists() {
        return in_tree;
    }
    // Ask the interpreter where the package landed: the path to site-packages embeds
    // the Python version, so it can't be derived from the executable's location.
    packaged_frontend(python).unwrap_or(in_tree)
}

fn packaged_frontend(python: &str) -> Option<PathBuf> {
    let out = std::process::Command::new(python)
        .arg("-c")
        .arg("import tubulin, os; print(os.path.join(os.path.dirname(tubulin.__file__), '_frontend'))")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let path = PathBuf::from(String::from_utf8(out.stdout).ok()?.trim());
    path.is_dir().then_some(path)
}

#[cfg(windows)]
const PYTHON_EXE: &str = "python.exe";
#[cfg(not(windows))]
const PYTHON_EXE: &str = "python3";

#[cfg(windows)]
const VENV_PYTHON: &str = ".venv/Scripts/python.exe";
#[cfg(not(windows))]
const VENV_PYTHON: &str = ".venv/bin/python";

/// `max_workers = 0` means auto: size the worker pool to the machine's cores.
fn effective_workers(configured: usize) -> usize {
    if configured == 0 {
        std::thread::available_parallelism().map_or(4, |n| n.get())
    } else {
        configured
    }
}
