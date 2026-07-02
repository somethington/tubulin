//! Shared application state handed to every request handler.

use std::sync::Arc;

use kinesin_protocol::AppSpec;

use crate::config::Config;
use crate::jobs::JobManager;
use crate::session::SessionManager;

pub struct AppState {
    pub config: Arc<Config>,
    pub spec: AppSpec,
    pub jobs: JobManager,
    pub sessions: SessionManager,
    /// Serializes on-demand bundle builds: concurrent `/bundle` requests for the
    /// same job would otherwise interleave writes on one `.partial` file.
    pub bundle_lock: tokio::sync::Mutex<()>,
}

impl AppState {
    pub fn new(config: Arc<Config>, spec: AppSpec) -> Self {
        let jobs = JobManager::new(config.clone());
        Self {
            config,
            spec,
            jobs,
            sessions: SessionManager::default(),
            bundle_lock: tokio::sync::Mutex::new(()),
        }
    }
}
