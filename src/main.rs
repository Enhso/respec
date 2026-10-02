//! Binary entry point: starts the Respec server on loopback.

use anyhow::Context;
use tracing_subscriber::EnvFilter;

use respec::server;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()))
        .init();

    let addr = server::addr(server::PORT);
    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .with_context(|| format!("binding to {addr}"))?;
    tracing::info!(%addr, "respec listening");
    axum::serve(listener, server::router())
        .await
        .context("serving requests")?;
    Ok(())
}
