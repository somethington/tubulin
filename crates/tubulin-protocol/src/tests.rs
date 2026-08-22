use serde_json::Map;

use crate::*;

#[test]
fn app_spec_matches_registration_shape() {
    // The exact JSON shape emitted by `python -m tubulin.register`.
    let json = r#"{
        "title": "CSV Analyzer",
        "nodes": [{"id": "button_1", "kind": "button", "props": {"label": "Run"}}],
        "actions": {"button_1": {"type": "submit_job", "job": "analyze",
            "args": [{"kind": "ref", "node": "file_upload_1", "field": "path"}],
            "result_slot": "slot_1"}},
        "jobs": {"analyze": {"module_file": "/abs/ui.py", "qualname": "analyze"}},
        "bindings": {"status_1": {"slot": "slot_1", "view": "status"}}
    }"#;
    let spec: AppSpec = serde_json::from_str(json).unwrap();
    assert_eq!(spec.title, "CSV Analyzer");
    match spec.actions.get("button_1").unwrap() {
        ActionSpec::SubmitJob {
            job,
            args,
            result_slot,
        } => {
            assert_eq!(job, "analyze");
            assert_eq!(result_slot, "slot_1");
            assert!(matches!(&args[0], ArgSpec::Ref { node, field }
                if node == "file_upload_1" && field == "path"));
        }
        _ => panic!("expected submit_job"),
    }
}

#[test]
fn worker_messages_parse() {
    let cases = [
        r#"{"event": "log", "message": "reading"}"#,
        r#"{"event": "progress", "progress": 50}"#,
        r#"{"event": "completed", "result": {"path": "storage/results/x.parquet"}}"#,
        r#"{"event": "failed", "error": "boom"}"#,
    ];
    for c in cases {
        serde_json::from_str::<WorkerMessage>(c)
            .unwrap_or_else(|e| panic!("failed to parse {c}: {e}"));
    }
}

#[test]
fn server_message_tagging() {
    let msg = ServerMessage::WidgetUpdate {
        node_id: "n".into(),
        props: Map::new(),
    };
    let json = serde_json::to_string(&msg).unwrap();
    assert!(json.contains("\"type\":\"widget_update\""));
}
