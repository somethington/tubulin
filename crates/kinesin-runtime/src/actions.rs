//! Resolving declarative actions against session state.

use kinesin_protocol::{ArgSpec, Binding};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;

use crate::jobs::JobSink;
use crate::session::Session;

/// Resolve a job's argument specs into concrete JSON values using session state.
pub fn resolve_args(args: &[ArgSpec], session: &Session) -> Result<Vec<Value>, String> {
    args.iter()
        .map(|arg| match arg {
            ArgSpec::Literal { value } => Ok(value.clone()),
            ArgSpec::Ref { node, field } => {
                let key = format!("{node}.{field}");
                session.get_state(&key).ok_or_else(|| {
                    format!("no value for '{key}' yet — did you upload a file first?")
                })
            }
        })
        .collect()
}

/// Build the sink describing which widgets a job's updates should drive, by
/// scanning the global bindings for nodes attached to `result_slot`. Status nodes
/// get lifecycle updates; every other view ("result_preview", "plot:<name>",
/// "table:<name>") is dispatched on completion.
pub fn build_sink(
    bindings: &HashMap<String, Binding>,
    result_slot: &str,
    session: &Arc<Session>,
) -> JobSink {
    let mut status_nodes = Vec::new();
    let mut result_nodes = Vec::new();
    for (node_id, binding) in bindings {
        if binding.slot != result_slot {
            continue;
        }
        if binding.view == "status" {
            status_nodes.push(node_id.clone());
        } else {
            result_nodes.push((node_id.clone(), binding.view.clone()));
        }
    }
    JobSink {
        tx: session.tx.clone(),
        status_nodes,
        result_nodes,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn test_session() -> Session {
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        Session::new("test".into(), tx)
    }

    #[test]
    fn resolves_literals_and_refs() {
        let session = test_session();
        session
            .set_state("file_upload_1.path".into(), json!("storage/uploads/x.csv"))
            .unwrap();

        let args = vec![
            ArgSpec::Ref {
                node: "file_upload_1".into(),
                field: "path".into(),
            },
            ArgSpec::Literal { value: json!(42) },
        ];
        let resolved = resolve_args(&args, &session).unwrap();
        assert_eq!(resolved, vec![json!("storage/uploads/x.csv"), json!(42)]);
    }

    #[test]
    fn missing_ref_is_an_error() {
        let session = test_session();
        let args = vec![ArgSpec::Ref {
            node: "file_upload_1".into(),
            field: "path".into(),
        }];
        assert!(resolve_args(&args, &session).is_err());
    }

    #[test]
    fn build_sink_groups_nodes_by_view() {
        let session = std::sync::Arc::new(test_session());
        let mut bindings = HashMap::new();
        bindings.insert(
            "status_1".into(),
            Binding {
                slot: "slot_1".into(),
                view: "status".into(),
            },
        );
        bindings.insert(
            "table_1".into(),
            Binding {
                slot: "slot_1".into(),
                view: "result_preview".into(),
            },
        );
        bindings.insert(
            "other".into(),
            Binding {
                slot: "slot_2".into(),
                view: "status".into(),
            },
        );
        let sink = build_sink(&bindings, "slot_1", &session);
        assert_eq!(sink.status_nodes, vec!["status_1".to_string()]);
        assert_eq!(
            sink.result_nodes,
            vec![("table_1".to_string(), "result_preview".to_string())]
        );
    }
}
