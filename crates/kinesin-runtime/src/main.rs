mod artifacts;
mod cli;
mod config;
mod http;
mod jobs;
mod registry;
mod reload;
mod session;
mod state;
mod workers;

use std::sync::Arc;

use tracing::error;
use tracing_subscriber::EnvFilter;

use crate::cli::{Command, RunArgs};
use crate::config::Config;
use crate::state::AppState;

#[tokio::main]
async fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("kinesin_runtime=info,info")),
        )
        .init();

    let command = match cli::parse() {
        Ok(c) => c,
        Err(msg) => {
            eprintln!("{msg}");
            std::process::exit(2);
        }
    };

    let result = match command {
        Command::Run(args) => run(args).await,
    };

    if let Err(e) = result {
        error!("{e:#}");
        eprintln!("error: {e:#}");
        std::process::exit(1);
    }
}

async fn run(args: RunArgs) -> anyhow::Result<()> {
    let config = Arc::new(Config::load(args.config, args.port)?);
    config.ensure_dirs()?;

    let spec = registry::register(&config, &args.ui_path).await?;
    tracing::info!(
        "loaded '{}' ({} nodes, {} jobs)",
        spec.title,
        spec.nodes.len(),
        spec.jobs.len()
    );

    let state = Arc::new(AppState::new(config, spec));

    // Watch the app's sources and re-register on save. `kinesin run` is the dev
    // command, so this is on unless the caller opts out.
    if args.reload {
        let ui_path = args.ui_path.clone();
        let watch_state = state.clone();
        tokio::spawn(async move { reload::watch(watch_state, ui_path).await });
    }

    http::serve(state).await
}
