pub mod actions;

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use kinesin_protocol::{AppSpec, ServerMessage};
use serde_json::Value;
use tokio::sync::mpsc::UnboundedSender;

/// Upper bound on one serialized state value; anything larger belongs on disk.
const MAX_STATE_VALUE_BYTES: usize = 64 * 1024;

pub struct Session {
    pub id: String,
    state: Mutex<HashMap<String, Value>>,
    /// result_slot -> runtime job_id.
    slots: Mutex<HashMap<String, String>>,
    /// Outbound channel to this session's WebSocket.
    pub tx: UnboundedSender<ServerMessage>,
}

impl Session {
    pub fn new(id: String, tx: UnboundedSender<ServerMessage>) -> Self {
        Self {
            id,
            state: Mutex::new(HashMap::new()),
            slots: Mutex::new(HashMap::new()),
            tx,
        }
    }

    /// Store a small scalar/path value; oversized values are rejected.
    pub fn set_state(&self, key: String, value: Value) -> Result<(), String> {
        let size = value.to_string().len();
        if size > MAX_STATE_VALUE_BYTES {
            return Err(format!(
                "refusing to store {size} bytes in session state for '{key}': \
                 session state is for ids/paths/small params only (Memory Rule 1)"
            ));
        }
        self.state.lock().unwrap().insert(key, value);
        Ok(())
    }

    pub fn get_state(&self, key: &str) -> Option<Value> {
        self.state.lock().unwrap().get(key).cloned()
    }

    pub fn set_slot(&self, slot: String, job_id: String) {
        self.slots.lock().unwrap().insert(slot, job_id);
    }

    /// Send a message to this session's browser; a closed socket is not an error.
    pub fn send(&self, msg: ServerMessage) {
        let _ = self.tx.send(msg);
    }

    /// Seed every input widget's default into state, so a job has values before the
    /// user touches anything. Called on connect and again after a hot reload — the
    /// browser re-seeds from the same spec, so both sides agree on the new defaults.
    pub fn seed_inputs(&self, spec: &AppSpec) {
        for node in &spec.nodes {
            if !is_input_kind(&node.kind) {
                continue;
            }
            if let Some(value) = node.props.get("value") {
                let _ = self.set_state(format!("{}.value", node.id), value.clone());
            }
        }
    }
}

/// Widget kinds whose `value` prop is a job argument. Grow this alongside `App`'s
/// input widgets in `python/kinesin/app.py` — a kind missing here means a job sees no
/// value until the user touches the widget.
fn is_input_kind(kind: &str) -> bool {
    matches!(
        kind,
        "checkbox" | "slider" | "number" | "select" | "multiselect" | "text_input" | "text_area"
    )
}

#[derive(Default)]
pub struct SessionManager {
    inner: Mutex<HashMap<String, Arc<Session>>>,
}

impl SessionManager {
    pub fn insert(&self, session: Arc<Session>) {
        self.inner
            .lock()
            .unwrap()
            .insert(session.id.clone(), session);
    }

    pub fn remove(&self, id: &str) {
        self.inner.lock().unwrap().remove(id);
    }

    pub fn count(&self) -> usize {
        self.inner.lock().unwrap().len()
    }

    /// Every live session, as a snapshot — the lock is not held while callers send.
    pub fn all(&self) -> Vec<Arc<Session>> {
        self.inner.lock().unwrap().values().cloned().collect()
    }
}
