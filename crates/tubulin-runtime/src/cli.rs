//! Tiny hand-rolled argument parser for `tubulin run <ui.py> [--port N] [--config P]`.

use std::path::PathBuf;

pub struct RunArgs {
    pub ui_path: PathBuf,
    pub port: Option<u16>,
    pub config: Option<PathBuf>,
    /// Re-register the app when its `.py` sources change. On unless `--no-reload`.
    pub reload: bool,
}

pub enum Command {
    Run(RunArgs),
}

const USAGE: &str = "tubulin — Python-first scientific app framework

USAGE:
    tubulin run <ui.py> [--port <PORT>] [--config <FILE>] [--no-reload]

ARGS:
    <ui.py>            Python file declaring the app

OPTIONS:
    --port <PORT>      Override the HTTP port (default 8000 / tubulin.toml)
    --config <FILE>    Path to a tubulin.toml (default: ./tubulin.toml)
    --no-reload        Don't re-register the app when its .py files change
    -h, --help         Print this help
";

pub fn parse() -> Result<Command, String> {
    let mut args = std::env::args().skip(1);
    let sub = args.next().ok_or_else(|| USAGE.to_string())?;
    match sub.as_str() {
        "run" => {
            let mut ui_path: Option<PathBuf> = None;
            let mut port: Option<u16> = None;
            let mut config: Option<PathBuf> = None;
            let mut reload = true;
            while let Some(arg) = args.next() {
                match arg.as_str() {
                    "--port" => {
                        let v = args.next().ok_or("--port requires a value")?;
                        port = Some(v.parse().map_err(|_| format!("invalid port: {v}"))?);
                    }
                    "--config" => {
                        config = Some(PathBuf::from(
                            args.next().ok_or("--config requires a value")?,
                        ));
                    }
                    "--no-reload" => reload = false,
                    "-h" | "--help" => return Err(USAGE.to_string()),
                    other if other.starts_with('-') => {
                        return Err(format!("unknown option: {other}\n\n{USAGE}"));
                    }
                    other => ui_path = Some(PathBuf::from(other)),
                }
            }
            let ui_path = ui_path.ok_or_else(|| format!("missing <ui.py>\n\n{USAGE}"))?;
            Ok(Command::Run(RunArgs {
                ui_path,
                port,
                config,
                reload,
            }))
        }
        "-h" | "--help" => Err(USAGE.to_string()),
        other => Err(format!("unknown command: {other}\n\n{USAGE}")),
    }
}
