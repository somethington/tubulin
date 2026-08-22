use arrow::array::{
    Array, BooleanArray, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array, Int8Array,
    LargeStringArray, RecordBatch, StringArray, UInt16Array, UInt32Array, UInt64Array, UInt8Array,
};
use arrow::util::display::{ArrayFormatter, FormatOptions};
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use serde_json::{json, Map, Number, Value};

/// Read the first `limit` rows + schema of a parquet file (non-finite floats become null).
pub(crate) fn read_preview(path: &str, limit: usize) -> Result<Map<String, Value>, String> {
    let file = std::fs::File::open(path).map_err(|e| format!("open failed: {e}"))?;
    let builder = ParquetRecordBatchReaderBuilder::try_new(file)
        .map_err(|e| format!("not a readable parquet: {e}"))?;

    let total = builder.metadata().file_metadata().num_rows().max(0) as usize;
    let columns: Vec<String> = builder
        .schema()
        .fields()
        .iter()
        .map(|f| f.name().clone())
        .collect();

    // One batch of `limit` rows is all we need.
    let mut reader = builder
        .with_batch_size(limit.max(1))
        .build()
        .map_err(|e| format!("parquet reader: {e}"))?;

    let rows = match reader.next() {
        Some(Ok(batch)) => batch_to_rows(&batch, limit)?,
        Some(Err(e)) => return Err(format!("read batch: {e}")),
        None => Vec::new(),
    };

    let mut out = Map::new();
    out.insert("columns".into(), json!(columns));
    out.insert("rows".into(), Value::Array(rows));
    out.insert("total_rows".into(), json!(total));
    out.insert("truncated".into(), Value::Bool(total > limit));
    Ok(out)
}

/// Convert up to `limit` rows of a `RecordBatch` into JSON arrays in column order.
fn batch_to_rows(batch: &RecordBatch, limit: usize) -> Result<Vec<Value>, String> {
    let n = batch.num_rows().min(limit);
    let opts = FormatOptions::default();
    // A string formatter per column covers types we don't special-case (dates, decimals, ...).
    let formatters: Vec<ArrayFormatter> = batch
        .columns()
        .iter()
        .map(|c| ArrayFormatter::try_new(c.as_ref(), &opts))
        .collect::<Result<_, _>>()
        .map_err(|e| format!("formatter: {e}"))?;

    let mut rows = Vec::with_capacity(n);
    for i in 0..n {
        let mut row = Vec::with_capacity(batch.num_columns());
        for (col, fmt) in batch.columns().iter().zip(&formatters) {
            row.push(cell_value(col.as_ref(), fmt, i));
        }
        rows.push(Value::Array(row));
    }
    Ok(rows)
}

/// JSON value for one cell: null stays null, native types map directly, the rest formats.
fn cell_value(array: &dyn Array, fmt: &ArrayFormatter, i: usize) -> Value {
    if array.is_null(i) {
        return Value::Null;
    }
    macro_rules! downcast {
        ($ty:ty) => {
            array.as_any().downcast_ref::<$ty>()
        };
    }
    if let Some(a) = downcast!(Float64Array) {
        return finite(a.value(i));
    }
    if let Some(a) = downcast!(Float32Array) {
        return finite(f64::from(a.value(i)));
    }
    if let Some(a) = downcast!(Int64Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(Int32Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(Int16Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(Int8Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(UInt64Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(UInt32Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(UInt16Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(UInt8Array) {
        return json!(a.value(i));
    }
    if let Some(a) = downcast!(BooleanArray) {
        return Value::Bool(a.value(i));
    }
    if let Some(a) = downcast!(StringArray) {
        return Value::String(a.value(i).to_string());
    }
    if let Some(a) = downcast!(LargeStringArray) {
        return Value::String(a.value(i).to_string());
    }
    // Fallback: human-readable formatting (dates, timestamps, decimals, lists, ...).
    Value::String(fmt.value(i).to_string())
}

/// A finite f64 as a JSON number; NaN and infinities become null.
fn finite(x: f64) -> Value {
    Number::from_f64(x).map_or(Value::Null, Value::Number)
}

#[cfg(test)]
mod tests;
