use serde::Deserialize;

#[derive(Debug, Default, Deserialize)]
pub struct RawConfig {
    #[serde(default)]
    pub server: RawServer,
    #[serde(default)]
    pub workers: RawWorkers,
    #[serde(default)]
    pub jobs: RawJobs,
    #[serde(default)]
    pub storage: RawStorage,
}

#[derive(Debug, Deserialize)]
pub struct RawServer {
    pub host: String,
    pub port: u16,
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
pub struct RawWorkers {
    pub max_workers: usize,
}

impl Default for RawWorkers {
    fn default() -> Self {
        Self { max_workers: 4 }
    }
}

#[derive(Debug, Deserialize)]
pub struct RawJobs {
    pub max_runtime_seconds: u64,
    pub max_queue_size: usize,
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
pub struct RawStorage {
    pub upload_dir: String,
    pub result_dir: String,
    pub cache_dir: String,
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
