use std::sync::Arc;

use kinesin_protocol::{ActionSpec, ClientMessage, ServerMessage};
use serde_json::{json, Map, Value};
use uuid::Uuid;

use crate::jobs::JobRequest;
use crate::session::actions::{build_sink, resolve_args};
use crate::session::Session;
use crate::state::AppState;

pub(super) fn handle_client_message(
    state: &Arc<AppState>,
    session: &Arc<Session>,
    msg: ClientMessage,
) {
    let ClientMessage::Event {
        node_id,
        event,
        payload,
    } = msg;

    // A finished upload records only its path; the file itself stays on disk.
    if event == "upload" {
        if let Some(path) = payload.get("path").and_then(Value::as_str) {
            if let Err(e) = session.set_state(format!("{node_id}.path"), json!(path)) {
                session.send(ServerMessage::Error { message: e });
            }
        }
    }

    // An input widget reports its current value (checkbox/slider/number/select).
    if event == "change" {
        if let Some(value) = payload.get("value") {
            if let Err(e) = session.set_state(format!("{node_id}.value"), value.clone()) {
                session.send(ServerMessage::Error { message: e });
            }
        }
    }

    if let Some(action) = state.spec.actions.get(&node_id) {
        run_action(state, session, action);
    }
}

fn run_action(state: &Arc<AppState>, session: &Arc<Session>, action: &ActionSpec) {
    match action {
        ActionSpec::SetText { target, value } => {
            let mut props = Map::new();
            props.insert("value".into(), Value::String(value.clone()));
            session.send(ServerMessage::WidgetUpdate {
                node_id: target.clone(),
                props,
            });
        }
        ActionSpec::SubmitJob {
            job,
            args,
            result_slot,
        } => {
            let Some(job_def) = state.spec.jobs.get(job).cloned() else {
                session.send(ServerMessage::Error {
                    message: format!("unknown job '{job}'"),
                });
                return;
            };
            let resolved = match resolve_args(args, session) {
                Ok(v) => v,
                Err(e) => {
                    session.send(ServerMessage::Error { message: e });
                    return;
                }
            };
            let job_id = Uuid::new_v4().to_string();
            session.set_slot(result_slot.clone(), job_id.clone());
            let sink = build_sink(&state.spec.bindings, result_slot, session);
            if let Err(e) = state.jobs.submit(JobRequest {
                job_id,
                job_def,
                args: resolved,
                sink,
            }) {
                session.send(ServerMessage::Error { message: e });
            }
        }
    }
}
