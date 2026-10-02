//! The HTTP server: the loopback address it binds and the API routes.

use std::net::{Ipv4Addr, SocketAddr};

use axum::{Json, Router, routing::get};
use serde_json::{Value, json};

/// The port Respec listens on. The operator's bookmark points here.
pub const PORT: u16 = 7377;

/// The address Respec listens on: IPv4 loopback only (ISC-6), never a
/// wildcard.
pub fn addr(port: u16) -> SocketAddr {
    SocketAddr::from((Ipv4Addr::LOCALHOST, port))
}

/// Builds the API router.
pub fn router() -> Router {
    Router::new().route("/api/health", get(health))
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream};

    #[tokio::test]
    async fn health_answers_on_loopback() {
        let listener = TcpListener::bind(addr(0)).await.expect("bind");
        let local = listener.local_addr().expect("local addr");
        assert!(local.ip().is_loopback());
        tokio::spawn(async move { axum::serve(listener, router()).await });

        let mut stream = TcpStream::connect(local).await.expect("connect");
        stream
            .write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            .await
            .expect("write request");
        let mut response = String::new();
        stream
            .read_to_string(&mut response)
            .await
            .expect("read response");

        assert!(response.starts_with("HTTP/1.1 200"), "{response}");
        assert!(response.ends_with(r#"{"status":"ok"}"#), "{response}");
    }
}
