//! The HTTP server: the loopback address it binds, the Host and Origin
//! checks, the API routes and the built web UI.

use std::net::{Ipv4Addr, SocketAddr};
use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::http::{Method, StatusCode, header};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tower_http::services::ServeDir;

use crate::keys::{ApiKey, KeyStore, Keys, Provider};

/// The port Respec listens on. The operator's bookmark points here.
pub const PORT: u16 = 7377;

/// The address Respec listens on: IPv4 loopback only (ISC-6), never a
/// wildcard.
pub fn addr(port: u16) -> SocketAddr {
    SocketAddr::from((Ipv4Addr::LOCALHOST, port))
}

/// Builds the router: the API under `/api`, the built web UI from `web_dir`
/// for everything else, and the Host and Origin checks over all of it.
/// `port` is the port the listener actually bound; the checks admit only
/// Respec's own loopback address on that port.
pub fn router(port: u16, keys: KeyStore, web_dir: PathBuf) -> Router {
    let guard = Arc::new(Guard::new(port));
    Router::new()
        .route("/api/health", get(health))
        .route("/api/settings", get(settings))
        .route("/api/settings/keys", post(save_keys))
        .fallback_service(ServeDir::new(web_dir))
        .with_state(Arc::new(keys))
        // The last layer added runs first, so the Host check precedes the
        // Origin check.
        .layer(middleware::from_fn_with_state(guard.clone(), check_origin))
        .layer(middleware::from_fn_with_state(guard, check_host))
}

/// The Host and Origin values that identify Respec's own page.
struct Guard {
    hosts: [String; 2],
    origins: [String; 2],
}

impl Guard {
    fn new(port: u16) -> Self {
        Self {
            hosts: [format!("127.0.0.1:{port}"), format!("localhost:{port}")],
            origins: [
                format!("http://127.0.0.1:{port}"),
                format!("http://localhost:{port}"),
            ],
        }
    }
}

/// ISC-46: rejects any request whose Host is not Respec's own loopback
/// address and port, which stops DNS rebinding. A missing Host is rejected.
async fn check_host(State(guard): State<Arc<Guard>>, req: Request, next: Next) -> Response {
    let host = req
        .headers()
        .get(header::HOST)
        .and_then(|v| v.to_str().ok());
    if host.is_some_and(|h| guard.hosts.iter().any(|allowed| allowed == h)) {
        return next.run(req).await;
    }
    tracing::warn!(?host, "rejected request: foreign Host");
    (StatusCode::FORBIDDEN, "forbidden: unexpected Host header").into_response()
}

/// ISC-47: rejects any request other than GET, HEAD or OPTIONS whose Origin
/// is not Respec's own page. A missing or `null` Origin is rejected too:
/// only the page posts, and browsers always send Origin on a POST.
async fn check_origin(State(guard): State<Arc<Guard>>, req: Request, next: Next) -> Response {
    if matches!(*req.method(), Method::GET | Method::HEAD | Method::OPTIONS) {
        return next.run(req).await;
    }
    let origin = req
        .headers()
        .get(header::ORIGIN)
        .and_then(|v| v.to_str().ok());
    if origin.is_some_and(|o| guard.origins.iter().any(|allowed| allowed == o)) {
        return next.run(req).await;
    }
    tracing::warn!(?origin, method = %req.method(), "rejected request: foreign Origin");
    (StatusCode::FORBIDDEN, "forbidden: unexpected Origin header").into_response()
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok" }))
}

/// What the API shows of the saved keys: the last four characters of each,
/// or null when none is set. Never a key itself (ISC-59).
#[derive(Serialize)]
struct Settings {
    keys: KeyTails,
}

#[derive(Serialize)]
struct KeyTails {
    openrouter: Option<String>,
    gemini: Option<String>,
}

impl Settings {
    fn from_keys(keys: &Keys) -> Self {
        let tail = |provider| keys.get(&provider).map(ApiKey::last_four);
        Self {
            keys: KeyTails {
                openrouter: tail(Provider::OpenRouter),
                gemini: tail(Provider::Gemini),
            },
        }
    }
}

/// The body of `POST /api/settings/keys`. It has no `Debug`, so a key in it
/// cannot be logged by accident.
#[derive(Deserialize)]
struct KeyUpdate {
    openrouter: Option<String>,
    gemini: Option<String>,
}

/// A plain-text error response.
type ApiError = (StatusCode, &'static str);

async fn settings(State(store): State<Arc<KeyStore>>) -> Result<Json<Settings>, ApiError> {
    let keys = store.load().map_err(|err| {
        tracing::error!(%err, "reading saved keys failed");
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "could not read saved keys",
        )
    })?;
    Ok(Json(Settings::from_keys(&keys)))
}

async fn save_keys(
    State(store): State<Arc<KeyStore>>,
    Json(update): Json<KeyUpdate>,
) -> Result<Json<Settings>, ApiError> {
    let mut updates = Keys::new();
    for (provider, value) in [
        (Provider::OpenRouter, update.openrouter),
        (Provider::Gemini, update.gemini),
    ] {
        let value = value.unwrap_or_default();
        if value.trim().is_empty() {
            continue;
        }
        let key = ApiKey::new(&value).map_err(|_| {
            (
                StatusCode::UNPROCESSABLE_ENTITY,
                "a key must be at least 8 characters",
            )
        })?;
        updates.insert(provider, key);
    }
    let updated: Vec<Provider> = updates.keys().copied().collect();
    let keys = store.save(updates).map_err(|err| {
        tracing::error!(%err, "saving keys failed");
        (StatusCode::INTERNAL_SERVER_ERROR, "could not save keys")
    })?;
    tracing::info!(providers = ?updated, "keys updated");
    Ok(Json(Settings::from_keys(&keys)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{Body, to_bytes};
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use std::path::Path;
    use tempfile::TempDir;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream};
    use tower::ServiceExt;

    /// Not bound to anything: `oneshot` tests need only a port to build the
    /// allowed Host and Origin values from.
    const PORT_UNDER_TEST: u16 = 4242;
    const FAKE_KEY: &str = "test-key-0123456789abcdef";

    /// A router over fresh temporary config and web directories. The config
    /// directory is a not-yet-created child, so saving has to create it.
    struct Fixture {
        _root: TempDir,
        config: PathBuf,
        web: PathBuf,
    }

    impl Fixture {
        fn new() -> Self {
            let root = tempfile::tempdir().expect("tempdir");
            let web = root.path().join("web");
            fs::create_dir(&web).expect("web dir");
            fs::write(web.join("index.html"), "<h1>stub page</h1>").expect("index.html");
            Self {
                config: root.path().join("Respec"),
                web,
                _root: root,
            }
        }

        fn router(&self) -> Router {
            router(
                PORT_UNDER_TEST,
                KeyStore::new(&self.config),
                self.web.clone(),
            )
        }
    }

    fn host(port: u16) -> String {
        format!("127.0.0.1:{port}")
    }

    fn origin(port: u16) -> String {
        format!("http://127.0.0.1:{port}")
    }

    fn get_with_host(path: &str, host: Option<&str>) -> Request<Body> {
        let mut req = Request::builder().uri(path);
        if let Some(host) = host {
            req = req.header(header::HOST, host);
        }
        req.body(Body::empty()).expect("request")
    }

    fn post_keys(origin: Option<&str>, body: &str) -> Request<Body> {
        let mut req = Request::builder()
            .method(Method::POST)
            .uri("/api/settings/keys")
            .header(header::HOST, host(PORT_UNDER_TEST))
            .header(header::CONTENT_TYPE, "application/json");
        if let Some(origin) = origin {
            req = req.header(header::ORIGIN, origin);
        }
        req.body(Body::from(body.to_owned())).expect("request")
    }

    async fn send(app: &Router, req: Request<Body>) -> (StatusCode, String) {
        let res = app.clone().oneshot(req).await.expect("response");
        let status = res.status();
        let body = to_bytes(res.into_body(), usize::MAX).await.expect("body");
        (
            status,
            String::from_utf8(body.to_vec()).expect("utf-8 body"),
        )
    }

    async fn save(app: &Router, body: &str) -> (StatusCode, String) {
        send(app, post_keys(Some(&origin(PORT_UNDER_TEST)), body)).await
    }

    async fn settings_body(app: &Router) -> String {
        let req = get_with_host("/api/settings", Some(&host(PORT_UNDER_TEST)));
        let (status, body) = send(app, req).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        body
    }

    fn mode(path: &Path) -> u32 {
        fs::metadata(path).expect("metadata").permissions().mode() & 0o777
    }

    #[tokio::test]
    async fn health_answers_on_loopback() {
        let fixture = Fixture::new();
        let listener = TcpListener::bind(addr(0)).await.expect("bind");
        let local = listener.local_addr().expect("local addr");
        assert!(local.ip().is_loopback());
        let app = router(
            local.port(),
            KeyStore::new(&fixture.config),
            fixture.web.clone(),
        );
        tokio::spawn(async move { axum::serve(listener, app).await });

        let mut stream = TcpStream::connect(local).await.expect("connect");
        let request = format!(
            "GET /api/health HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
            host(local.port())
        );
        stream
            .write_all(request.as_bytes())
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

    #[tokio::test]
    async fn foreign_host_rejected() {
        let fixture = Fixture::new();
        let app = fixture.router();
        for path in ["/api/health", "/"] {
            let (status, _) = send(&app, get_with_host(path, Some("evil.example"))).await;
            assert_eq!(status, StatusCode::FORBIDDEN, "{path}");
        }
        // The right host with the wrong port is foreign too.
        let wrong_port = host(PORT_UNDER_TEST + 1);
        let (status, _) = send(&app, get_with_host("/api/health", Some(&wrong_port))).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn missing_host_rejected() {
        let fixture = Fixture::new();
        let app = fixture.router();
        let (status, _) = send(&app, get_with_host("/api/health", None)).await;
        assert_eq!(status, StatusCode::FORBIDDEN);
    }

    #[tokio::test]
    async fn own_host_accepted() {
        let fixture = Fixture::new();
        let app = fixture.router();
        for own in [
            host(PORT_UNDER_TEST),
            format!("localhost:{PORT_UNDER_TEST}"),
        ] {
            let (status, _) = send(&app, get_with_host("/api/health", Some(&own))).await;
            assert_eq!(status, StatusCode::OK, "{own}");
        }
    }

    #[tokio::test]
    async fn foreign_origin_rejected() {
        let fixture = Fixture::new();
        let app = fixture.router();
        let body = format!(r#"{{"openrouter":"{FAKE_KEY}"}}"#);
        for bad in [Some("https://evil.example"), Some("null"), None] {
            let (status, _) = send(&app, post_keys(bad, &body)).await;
            assert_eq!(status, StatusCode::FORBIDDEN, "origin {bad:?}");
        }
        assert!(!fixture.config.exists(), "a rejected request saved keys");

        let (status, _) = send(&app, post_keys(Some(&origin(PORT_UNDER_TEST)), &body)).await;
        assert_eq!(status, StatusCode::OK);
        let localhost = format!("http://localhost:{PORT_UNDER_TEST}");
        let (status, _) = send(&app, post_keys(Some(&localhost), &body)).await;
        assert_eq!(status, StatusCode::OK);
    }

    #[tokio::test]
    async fn saved_keys_are_private_and_masked() {
        let fixture = Fixture::new();
        let app = fixture.router();

        let (status, body) = save(&app, &format!(r#"{{"openrouter":"{FAKE_KEY}"}}"#)).await;
        assert_eq!(status, StatusCode::OK, "{body}");
        assert!(!body.contains(FAKE_KEY), "{body}");
        assert!(body.contains(r#""openrouter":"cdef""#), "{body}");
        assert!(body.contains(r#""gemini":null"#), "{body}");

        let body = settings_body(&app).await;
        assert!(!body.contains(FAKE_KEY), "{body}");
        assert!(body.contains(r#""openrouter":"cdef""#), "{body}");

        assert_eq!(mode(&fixture.config), 0o700);
        assert_eq!(mode(&fixture.config.join("keys.json")), 0o600);
        let files: Vec<_> = fs::read_dir(&fixture.config)
            .expect("read config dir")
            .map(|e| e.expect("entry").file_name())
            .collect();
        assert_eq!(files, ["keys.json"], "a temp file was left behind");
    }

    #[tokio::test]
    async fn keys_persist_and_updates_merge() {
        let fixture = Fixture::new();
        let (status, _) = save(
            &fixture.router(),
            &format!(r#"{{"openrouter":"{FAKE_KEY}"}}"#),
        )
        .await;
        assert_eq!(status, StatusCode::OK);

        // A second router over the same config dir sees the saved key.
        let second = fixture.router();
        assert!(
            settings_body(&second)
                .await
                .contains(r#""openrouter":"cdef""#)
        );

        // A gemini-only update, a blank value and an absent field all leave
        // the openrouter key alone. Values are trimmed.
        let (status, body) = save(
            &second,
            r#"{"gemini":"  gemini-key-wxyz  ","openrouter":"  "}"#,
        )
        .await;
        assert_eq!(status, StatusCode::OK, "{body}");
        assert!(body.contains(r#""openrouter":"cdef""#), "{body}");
        assert!(body.contains(r#""gemini":"wxyz""#), "{body}");
        let stored = fs::read_to_string(fixture.config.join("keys.json")).expect("keys.json");
        assert!(stored.contains(FAKE_KEY) && stored.contains(r#""gemini-key-wxyz""#));
    }

    #[tokio::test]
    async fn short_key_gives_422_and_saves_nothing() {
        let fixture = Fixture::new();
        let app = fixture.router();
        // The valid openrouter key must not be saved when gemini is refused.
        let body = format!(r#"{{"openrouter":"{FAKE_KEY}","gemini":"1234567"}}"#);
        let (status, message) = save(&app, &body).await;
        assert_eq!(status, StatusCode::UNPROCESSABLE_ENTITY);
        assert!(!message.contains("1234567"), "{message}");
        assert!(!fixture.config.exists(), "a refused save wrote to disk");
    }

    #[tokio::test]
    async fn unreadable_key_file_gives_500_without_its_contents() {
        let fixture = Fixture::new();
        fs::create_dir(&fixture.config).expect("config dir");
        fs::write(
            fixture.config.join("keys.json"),
            r#"{"openrouter": "sk-leaky-0123456789", oops"#,
        )
        .expect("write");
        let req = get_with_host("/api/settings", Some(&host(PORT_UNDER_TEST)));
        let (status, body) = send(&fixture.router(), req).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
        assert!(!body.contains("sk-leaky"), "{body}");
    }

    #[tokio::test]
    async fn root_serves_index_html() {
        let fixture = Fixture::new();
        let app = fixture.router();
        let (status, body) = send(&app, get_with_host("/", Some(&host(PORT_UNDER_TEST)))).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body, "<h1>stub page</h1>");
    }
}
