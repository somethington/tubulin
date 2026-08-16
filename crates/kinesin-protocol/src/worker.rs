use std::collections::HashMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl JobStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            JobStatus::Queued => "queued",
            JobStatus::Running => "running",
            JobStatus::Completed => "completed",
            JobStatus::Failed => "failed",
            JobStatus::Cancelled => "cancelled",
        }
    }
}

/// One JSON object per line on a worker's stdout.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case")]
pub enum WorkerMessage {
    Log { message: String },
    Progress { progress: u8 },
    Completed { result: WorkerResult },
    Failed { error: String },
}

/// Terminal payload of a successful job.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WorkerResult {
    /// Path of the job's primary result on disk, if any.
    #[serde(default)]
    pub path: Option<String>,
    /// Named secondary artifacts (plots, tables): name -> path.
    #[serde(default)]
    pub outputs: HashMap<String, String>,
}
