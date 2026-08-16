use super::*;
use arrow::array::{Float64Array, Int64Array, StringArray};
use arrow::datatypes::{DataType, Field, Schema};
use parquet::arrow::ArrowWriter;
use std::sync::Arc;

fn write_parquet(path: &str, rows: usize) -> RecordBatch {
    let f: Float64Array = (0..rows)
        .map(|i| match i {
            1 => Some(f64::NAN), // -> null
            2 => None,           // -> null
            n => Some(n as f64 + 0.5),
        })
        .collect();
    let i: Int64Array = (0..rows as i64).map(Some).collect();
    let s: StringArray = (0..rows)
        .map(|n| if n == 0 { Some("a") } else { Some("z") })
        .collect();
    let schema = Arc::new(Schema::new(vec![
        Field::new("f", DataType::Float64, true),
        Field::new("i", DataType::Int64, true),
        Field::new("s", DataType::Utf8, true),
    ]));
    let batch =
        RecordBatch::try_new(schema.clone(), vec![Arc::new(f), Arc::new(i), Arc::new(s)]).unwrap();
    let file = std::fs::File::create(path).unwrap();
    let mut w = ArrowWriter::try_new(file, schema, None).unwrap();
    w.write(&batch).unwrap();
    w.close().unwrap();
    batch
}

#[test]
fn reads_head_with_nan_and_null_as_null() {
    let path = format!(
        "{}/kin_preview_test.parquet",
        std::env::temp_dir().display()
    );
    write_parquet(&path, 5);

    let p = read_preview(&path, 100).unwrap();
    assert_eq!(p["columns"], json!(["f", "i", "s"]));
    assert_eq!(p["total_rows"], json!(5));
    assert_eq!(p["truncated"], Value::Bool(false));

    let rows = p["rows"].as_array().unwrap();
    assert_eq!(rows.len(), 5);
    assert_eq!(rows[0], json!([0.5, 0, "a"]));
    assert_eq!(rows[1][0], Value::Null); // NaN -> null
    assert_eq!(rows[2][0], Value::Null); // null -> null
    assert_eq!(rows[3], json!([3.5, 3, "z"]));
    let _ = std::fs::remove_file(&path);
}

#[test]
#[ignore]
fn reads_polars_written_parquet() {
    let path = std::env::var("KINESIN_TEST_PARQUET").expect("set KINESIN_TEST_PARQUET");
    let p = read_preview(&path, 100).unwrap();
    assert_eq!(p["columns"], json!(["name", "score", "count", "flag"]));
    assert_eq!(p["total_rows"], json!(3));
    let rows = p["rows"].as_array().unwrap();
    assert_eq!(rows[0], json!(["alpha", 1.5, 10, true]));
    assert_eq!(rows[1][1], Value::Null); // NaN -> null
    assert_eq!(rows[2], json!(["gamma", 3.25, 30, true]));
}

#[test]
fn truncates_to_limit() {
    let path = format!(
        "{}/kin_preview_trunc.parquet",
        std::env::temp_dir().display()
    );
    write_parquet(&path, 250);

    let p = read_preview(&path, 100).unwrap();
    assert_eq!(p["total_rows"], json!(250));
    assert_eq!(p["truncated"], Value::Bool(true));
    assert_eq!(p["rows"].as_array().unwrap().len(), 100);
    let _ = std::fs::remove_file(&path);
}
