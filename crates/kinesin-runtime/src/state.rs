use std::sync::{Arc, RwLock};

use kinesin_protocol::AppSpec;

use crate::config::Config;
use crate::jobs::JobManager;
use crate::session::SessionManager;

pub struct AppState {
    pub config: Arc<Config>,
    /// The live app description. Behind a lock because `kinesin run` re-registers the
    /// app when its source changes (see `reload.rs`); readers take a cheap `Arc` clone
    /// so a reload swapping the spec never tears a request that is mid-flight.
    spec: RwLock<Arc<AppSpec>>,
    pub jobs: JobManager,
    pub sessions: SessionManager,
    /// Serializes on-demand bundle builds so one `.partial` file has one writer.
    pub bundle_lock: tokio::sync::Mutex<()>,
}

impl AppState {
    pub fn new(config: Arc<Config>, spec: AppSpec) -> Self {
        let jobs = JobManager::new(config.clone());
        Self {
            config,
            spec: RwLock::new(Arc::new(spec)),
            jobs,
            sessions: SessionManager::default(),
            bundle_lock: tokio::sync::Mutex::new(()),
        }
    }

    /// The current spec. Hold the returned `Arc` for the whole of one operation rather
    /// than calling this repeatedly, so a concurrent reload can't change it midway.
    pub fn spec(&self) -> Arc<AppSpec> {
        self.spec.read().unwrap().clone()
    }

    /// Install a newly registered spec. In-flight jobs keep the `JobDef` they started
    /// with (it was cloned at submit time), so they finish against the old definition.
    pub fn set_spec(&self, spec: AppSpec) -> Arc<AppSpec> {
        let spec = Arc::new(spec);
        *self.spec.write().unwrap() = spec.clone();
        spec
    }
}
