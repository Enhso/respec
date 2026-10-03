//! Binary entry point: `respec` starts the server on loopback, and
//! `respec setup` installs the login agent.

use std::path::PathBuf;
use std::process::ExitCode;

use anyhow::Context;
use tracing_subscriber::EnvFilter;

use respec::keys::KeyStore;
use respec::server;
use respec::setup;
use respec::worker::Worker;

/// The built web UI: `RESPEC_WEB_DIR` if set, else `web/dist`.
fn web_dir() -> PathBuf {
    std::env::var_os("RESPEC_WEB_DIR").map_or_else(|| PathBuf::from("web/dist"), PathBuf::from)
}

/// The worker executable: `RESPEC_WORKER` if set, else the project virtualenv's
/// `respec-worker` script (relative to the working directory, like the web
/// dir). Running the script directly needs no uv or PATH at run time.
fn worker_path() -> PathBuf {
    std::env::var_os("RESPEC_WORKER").map_or_else(
        || PathBuf::from("python/.venv/bin/respec-worker"),
        PathBuf::from,
    )
}

fn main() -> anyhow::Result<ExitCode> {
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    match args.as_slice() {
        [] => {
            tokio::runtime::Runtime::new()
                .context("starting the async runtime")?
                .block_on(serve())?;
            Ok(ExitCode::SUCCESS)
        }
        [arg] if arg == "setup" => Ok(match setup::run() {
            Ok(()) => ExitCode::SUCCESS,
            Err(err) => {
                eprintln!("respec setup: {err:#}");
                ExitCode::FAILURE
            }
        }),
        _ => {
            eprintln!("usage: respec [setup]");
            Ok(ExitCode::from(2))
        }
    }
}

async fn serve() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    let config_dir = respec::config_dir()?;
    let web_dir = web_dir();
    let worker_path = worker_path();
    tracing::info!(
        config_dir = %config_dir.display(),
        web_dir = %web_dir.display(),
        worker = %worker_path.display(),
        "paths"
    );

    let addr = server::addr(server::PORT);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .with_context(|| format!("binding to {addr}"))?;
    let port = listener
        .local_addr()
        .context("reading bound address")?
        .port();
    tracing::info!(%addr, "respec listening");
    axum::serve(
        listener,
        server::router(
            port,
            KeyStore::new(config_dir),
            web_dir,
            Worker::new(worker_path),
        ),
    )
    .await
    .context("serving requests")?;
    Ok(())
}
