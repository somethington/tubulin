use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

use kinesin_protocol::JobStatus;

/// Retained job records; the oldest are evicted beyond this cap so memory stays bounded.
const MAX_RECORDS: usize = 1000;

/// Introspection record for a submitted job (no reader yet; a /jobs endpoint is planned).
#[derive(Clone)]
#[allow(dead_code)]
pub(crate) struct JobRecord {
    pub status: JobStatus,
    pub result_path: Option<String>,
}

#[derive(Clone, Default)]
pub(crate) struct Records {
    inner: Arc<Mutex<Store>>,
}

#[derive(Default)]
struct Store {
    map: HashMap<String, JobRecord>,
    order: VecDeque<String>,
}

impl Records {
    pub fn insert(&self, id: String, record: JobRecord) {
        let mut store = self.inner.lock().unwrap();
        while store.order.len() >= MAX_RECORDS {
            if let Some(old) = store.order.pop_front() {
                store.map.remove(&old);
            }
        }
        store.order.push_back(id.clone());
        store.map.insert(id, record);
    }

    pub fn set_status(&self, job_id: &str, status: JobStatus) {
        if let Some(rec) = self.inner.lock().unwrap().map.get_mut(job_id) {
            rec.status = status;
        }
    }

    pub fn set_result_path(&self, job_id: &str, path: Option<String>) {
        if let Some(rec) = self.inner.lock().unwrap().map.get_mut(job_id) {
            rec.result_path = path;
        }
    }
}
