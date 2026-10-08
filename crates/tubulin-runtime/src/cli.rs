//! Tiny hand-rolled argument parser for `tubulin run`, `tubulin new` and `--version`.

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
    /// Scaffold a starter app into this directory.
    New(PathBuf),
    Version,
}

/// The version of this build, as `tubulin --version` prints it.
pub const VERSION: &str = concat!("tubulin ", env!("CARGO_PKG_VERSION"));

const USAGE: &str = "tubulin — Python-first scientific app framework

USAGE:
    tubulin run <ui.py> [--port <PORT>] [--config <FILE>] [--no-reload]
    tubulin new <dir>
    tubulin --version

COMMANDS:
    run <ui.py>        Serve the app declared in <ui.py>
    new <dir>          Write a runnable starter app (ui.py, tubulin.toml) into <dir>

OPTIONS (run):
    --port <PORT>      Override the HTTP port (default 8000 / tubulin.toml)
    --config <FILE>    Path to a tubulin.toml (default: ./tubulin.toml)
    --no-reload        Don't re-register the app when its .py files change

    -h, --help         Print this help
    -V, --version      Print the version
";

pub fn parse() -> Result<Command, String> {
    parse_from(std::env::args().skip(1))
}

fn parse_from(mut args: impl Iterator<Item = String>) -> Result<Command, String> {
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
        "new" => match (args.next(), args.next()) {
            (Some(dir), None) if !dir.starts_with('-') => Ok(Command::New(PathBuf::from(dir))),
            (Some(h), None) if h == "-h" || h == "--help" => Err(USAGE.to_string()),
            (None, _) => Err(format!("missing <dir>\n\n{USAGE}")),
            _ => Err(format!("usage: tubulin new <dir>\n\n{USAGE}")),
        },
        "-V" | "--version" => Ok(Command::Version),
        "-h" | "--help" => Err(USAGE.to_string()),
        other => Err(format!("unknown command: {other}\n\n{USAGE}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(args: &[&str]) -> Result<Command, String> {
        parse_from(args.iter().map(|s| s.to_string()))
    }

    #[test]
    fn version_flags() {
        assert!(matches!(parse(&["--version"]), Ok(Command::Version)));
        assert!(matches!(parse(&["-V"]), Ok(Command::Version)));
        assert_eq!(VERSION, format!("tubulin {}", env!("CARGO_PKG_VERSION")));
    }

    #[test]
    fn new_takes_exactly_one_dir() {
        match parse(&["new", "my_app"]) {
            Ok(Command::New(dir)) => assert_eq!(dir, PathBuf::from("my_app")),
            _ => panic!("expected new"),
        }
        assert!(parse(&["new"]).is_err());
        assert!(parse(&["new", "a", "b"]).is_err());
        assert!(parse(&["new", "--port"]).is_err());
    }

    #[test]
    fn run_still_parses() {
        match parse(&["run", "ui.py", "--port", "9000", "--no-reload"]) {
            Ok(Command::Run(a)) => {
                assert_eq!(a.ui_path, PathBuf::from("ui.py"));
                assert_eq!(a.port, Some(9000));
                assert!(!a.reload);
            }
            _ => panic!("expected run"),
        }
    }
}
