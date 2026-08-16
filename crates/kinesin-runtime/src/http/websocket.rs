use std::sync::Arc;

use axum::extract::ws::{Message, WebSocket};
use futures::{SinkExt, StreamExt};
use kinesin_protocol::{ClientMessage, ServerMessage};
use tracing::{debug, info};
use uuid::Uuid;

use crate::http::events::handle_client_message;
use crate::session::Session;
use crate::state::AppState;

pub(super) async fn handle_socket(socket: WebSocket, state: Arc<AppState>) {
    let session_id = Uuid::new_v4().to_string();
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<ServerMessage>();
    let session = Arc::new(Session::new(session_id.clone(), tx));
    state.sessions.insert(session.clone());
    info!(session = %session_id, total = state.sessions.count(), "session connected");

    // Seed input-widget defaults so jobs have values before any interaction.
    for node in &state.spec.nodes {
        if matches!(
            node.kind.as_str(),
            "checkbox" | "slider" | "number" | "select"
        ) {
            if let Some(value) = node.props.get("value") {
                let _ = session.set_state(format!("{}.value", node.id), value.clone());
            }
        }
    }

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

    // Whichever half ends first tears down the other.
    tokio::select! {
        _ = &mut writer => reader.abort(),
        _ = &mut reader => writer.abort(),
    }

    state.sessions.remove(&session_id);
    info!(session = %session_id, total = state.sessions.count(), "session disconnected");
}
