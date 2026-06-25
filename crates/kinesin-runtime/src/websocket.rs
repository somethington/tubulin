//! Per-session WebSocket loop: send the UI tree on connect, then translate browser
//! events into action execution and push widget updates back.

use std::sync::Arc;

use axum::extract::ws::{Message, WebSocket};
use futures::{SinkExt, StreamExt};
use kinesin_protocol::{ActionSpec, ClientMessage, ServerMessage};
use serde_json::{json, Map, Value};
use tracing::{debug, info};
use uuid::Uuid;

use crate::actions::{build_sink, resolve_args};
use crate::jobs::JobRequest;
use crate::session::Session;
use crate::state::AppState;

pub async fn handle_socket(socket: WebSocket, state: Arc<AppState>) {
    let session_id = Uuid::new_v4().to_string();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<ServerMessage>();
    let session = Arc::new(Session::new(session_id.clone(), tx));
    state.sessions.insert(session.clone());
    info!(session = %session_id, total = state.sessions.count(), "session connected");

    // Seed input-widget defaults so jobs have values even before any interaction.
    for node in &state.spec.nodes {
        if matches!(node.kind.as_str(), "checkbox" | "slider" | "number" | "select") {
            if let Some(value) = node.props.get("value") {
                let _ = session.set_state(format!("{}.value", node.id), value.clone());
            }
        }
    }

    // Send the full UI tree first.
    session.send(ServerMessage::UiTree {
        session_id: session_id.clone(),
        title: state.spec.title.clone(),
        nodes: state.spec.nodes.clone(),
    });

    let (mut sink, mut stream) = socket.split();

    let mut writer = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            match serde_json::to_string(&msg) {
                Ok(text) => {
                    if sink.send(Message::Text(text)).await.is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });

    let reader_state = state.clone();
    let reader_session = session.clone();
    let mut reader = tokio::spawn(async move {
        while let Some(Ok(msg)) = stream.next().await {
            match msg {
                Message::Text(text) => match serde_json::from_str::<ClientMessage>(&text) {
                    Ok(client) => handle_client_message(&reader_state, &reader_session, client),
                    Err(e) => debug!(error = %e, "ignoring unparseable client message"),
                },
                Message::Close(_) => break,
                _ => {}
            }
        }
    });

    // Whichever half ends first, tear down the other.
    tokio::select! {
        _ = &mut writer => reader.abort(),
        _ = &mut reader => writer.abort(),
    }

    state.sessions.remove(&session_id);
    info!(session = %session_id, total = state.sessions.count(), "session disconnected");
}

fn handle_client_message(state: &Arc<AppState>, session: &Arc<Session>, msg: ClientMessage) {
    let ClientMessage::Event {
        node_id,
        event,
        payload,
    } = msg;

    // A finished upload records its path into session state (Rule 2 keeps the file
    // on disk; only the path lives in the session).
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
