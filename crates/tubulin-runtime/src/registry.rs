use std::path::Path;

use anyhow::{bail, Context};
use tubulin_protocol::AppSpec;

use crate::config::Config;

pub async fn register(config: &Config, ui_path: &Path) -> anyhow::Result<AppSpec> {
    if !ui_path.exists() {
        bail!("UI file not found: {}", ui_path.display());
    }

    let output = config
        .python_command()
        .arg("-m")
        .arg("tubulin.register")
        .arg(ui_path)
        .env("TUBULIN_REGISTER", "1")
        .current_dir(&config.project_root)
        .output()
        .await
        .with_context(|| format!("failed to launch python ({})", config.python))?;

    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        bail!(
            "registration pass failed (status {}):\n{}",
            output.status,
            stderr.trim()
        );
    }

    let stdout = String::from_utf8(output.stdout).context("registration output not UTF-8")?;
    let spec: AppSpec = serde_json::from_str(stdout.trim()).with_context(|| {
        format!(
            "could not parse app spec from registration output.\nstdout: {}\nstderr: {}",
            stdout.trim(),
            stderr.trim()
        )
    })?;

    Ok(spec)
}
