//! `respec setup`: install the worker's Python environment and register a
//! per-user LaunchAgent so Respec starts at every login.
//!
//! The agent's plist holds absolute paths only. launchd gives a job no shell
//! `PATH`, and neither `HOME` nor the working directory can be relied on, so
//! every path the binary needs is written into the plist.

use std::ffi::OsString;
use std::fs;
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use anyhow::{Context, bail, ensure};

use crate::server;

/// The LaunchAgent's label, which is also its plist's file stem.
pub const LABEL: &str = "io.github.enhso.respec";

/// `launchctl` and `id` by absolute path: they live at fixed places on
/// macOS, so setup does not depend on the caller's `PATH` either.
const LAUNCHCTL: &str = "/bin/launchctl";
const ID: &str = "/usr/bin/id";

/// How long to wait for Respec to answer after the agent is registered.
const HEALTH_TIMEOUT: Duration = Duration::from_secs(20);

/// How long to wait for a replaced agent to finish unloading.
const UNLOAD_TIMEOUT: Duration = Duration::from_secs(10);

const UV_MISSING: &str = "uv was not found. Install it with \
    `curl -LsSf https://astral.sh/uv/install.sh | sh`, or set RESPEC_UV to its path, then run setup again";

/// Runs setup for the bundle this binary sits in. Prints progress as it goes
/// and returns an error with a plain message on the first failure.
pub fn run() -> anyhow::Result<()> {
    if !cfg!(target_os = "macos") {
        bail!("this command installs a macOS LaunchAgent and only runs on a Mac");
    }
    let home = dirs::home_dir().context("cannot find the home directory")?;
    let exe = std::env::current_exe()
        .and_then(|exe| exe.canonicalize())
        .context("cannot locate the respec binary")?;
    let app = exe
        .parent()
        .context("the respec binary has no parent directory")?;
    let config_dir = crate::config_dir()?;
    let uv = find_uv(
        std::env::var_os("RESPEC_UV"),
        std::env::var_os("PATH"),
        &home,
    )
    .context(UV_MISSING)?;

    // Render before the slow sync, so a bad path fails at once.
    let plist = render_plist(app, &config_dir, &log_path(&home))?;

    println!("Installing the worker with {}", uv.display());
    let status = Command::new(&uv)
        .args(["sync", "--frozen", "--no-dev", "--directory"])
        .arg(app.join("python"))
        .status()
        .with_context(|| format!("running {}", uv.display()))?;
    ensure!(status.success(), "uv sync failed ({status})");

    let plist_file = plist_path(&home);
    for file in [&plist_file, &log_path(&home)] {
        let dir = file.parent().context("a setup path has no parent")?;
        fs::create_dir_all(dir).with_context(|| format!("creating {}", dir.display()))?;
    }
    fs::write(&plist_file, plist).with_context(|| format!("writing {}", plist_file.display()))?;

    println!("Registering the login agent {LABEL}");
    register(&current_uid()?, &plist_file)?;

    let addr = server::addr(server::PORT);
    if !wait_for_health(addr, HEALTH_TIMEOUT) {
        bail!(
            "Respec did not answer at http://{addr} within {} s; see {}",
            HEALTH_TIMEOUT.as_secs(),
            log_path(&home).display()
        );
    }
    println!("Respec is running at http://{addr}");
    Ok(())
}

/// `~/Library/LaunchAgents/<label>.plist` for the given home.
pub fn plist_path(home: &Path) -> PathBuf {
    home.join("Library/LaunchAgents")
        .join(format!("{LABEL}.plist"))
}

/// `~/Library/Logs/Respec/respec.log` for the given home: where launchd
/// sends the server's stdout and stderr.
pub fn log_path(home: &Path) -> PathBuf {
    home.join("Library/Logs/Respec/respec.log")
}

/// Finds uv: `respec_uv` (the value of `RESPEC_UV`) first, then a `uv` in
/// a `PATH` directory, then `~/.local/bin/uv`, then `~/.cargo/bin/uv`. The
/// first candidate that is an executable file wins, so a `RESPEC_UV` that
/// names nothing falls through to the rest. Relative `PATH` entries are
/// ignored.
pub fn find_uv(
    respec_uv: Option<OsString>,
    path: Option<OsString>,
    home: &Path,
) -> Option<PathBuf> {
    let from_env = respec_uv.map(PathBuf::from);
    let on_path = path
        .into_iter()
        .flat_map(|path| std::env::split_paths(&path).collect::<Vec<_>>())
        .filter(|dir| dir.is_absolute())
        .map(|dir| dir.join("uv"));
    let known = [home.join(".local/bin/uv"), home.join(".cargo/bin/uv")];
    from_env
        .into_iter()
        .chain(on_path)
        .chain(known)
        .find(|candidate| is_executable_file(candidate))
}

fn is_executable_file(path: &Path) -> bool {
    fs::metadata(path).is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
}

/// Renders the LaunchAgent plist for a bundle at `app`.
///
/// It runs `<app>/respec` from `<app>` and sets the three paths the binary
/// reads (`RESPEC_CONFIG_DIR`, `RESPEC_WEB_DIR`, `RESPEC_WORKER`) explicitly,
/// starts at load, restarts the server if it exits, and sends stdout and
/// stderr to `log`. Fails if any path is relative or not valid UTF-8; values
/// are XML-escaped.
pub fn render_plist(app: &Path, config_dir: &Path, log: &Path) -> anyhow::Result<String> {
    let program = plist_value(&app.join("respec"))?;
    let working_dir = plist_value(app)?;
    let config_dir = plist_value(config_dir)?;
    let web_dir = plist_value(&app.join("web/dist"))?;
    let worker = plist_value(&app.join("python/.venv/bin/respec-worker"))?;
    let log = plist_value(log)?;
    Ok(format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{LABEL}</string>
	<key>ProgramArguments</key>
	<array>
		<string>{program}</string>
	</array>
	<key>WorkingDirectory</key>
	<string>{working_dir}</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>RESPEC_CONFIG_DIR</key>
		<string>{config_dir}</string>
		<key>RESPEC_WEB_DIR</key>
		<string>{web_dir}</string>
		<key>RESPEC_WORKER</key>
		<string>{worker}</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>StandardOutPath</key>
	<string>{log}</string>
	<key>StandardErrorPath</key>
	<string>{log}</string>
</dict>
</plist>
"#
    ))
}

/// A path as an XML-escaped plist string, refusing relative paths.
fn plist_value(path: &Path) -> anyhow::Result<String> {
    ensure!(
        path.is_absolute(),
        "{} is not an absolute path, and launchd needs absolute paths",
        path.display()
    );
    let text = path
        .to_str()
        .with_context(|| format!("{} is not valid UTF-8", path.display()))?;
    Ok(xml_escape(text))
}

fn xml_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&apos;"),
            other => out.push(other),
        }
    }
    out
}

/// The current user's numeric id, from `id -u`.
fn current_uid() -> anyhow::Result<String> {
    let output = Command::new(ID)
        .arg("-u")
        .output()
        .with_context(|| format!("running {ID} -u"))?;
    ensure!(
        output.status.success(),
        "{ID} -u failed ({})",
        output.status
    );
    let uid = String::from_utf8_lossy(&output.stdout).trim().to_owned();
    ensure!(
        !uid.is_empty() && uid.bytes().all(|byte| byte.is_ascii_digit()),
        "unexpected output from {ID} -u: {uid:?}"
    );
    Ok(uid)
}

/// Loads the agent into the user's GUI domain, replacing one already loaded
/// so a second run leaves exactly one agent.
fn register(uid: &str, plist: &Path) -> anyhow::Result<()> {
    let target = format!("gui/{uid}/{LABEL}");
    // Nothing is loaded on a first run, so a failing bootout is expected.
    let _ = launchctl_quiet(&["bootout", &target]);
    // Bootout returns before the old job is fully gone, and bootstrapping
    // over it fails with an I/O error.
    let deadline = Instant::now() + UNLOAD_TIMEOUT;
    while launchctl_quiet(&["print", &target]) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(200));
    }
    let status = Command::new(LAUNCHCTL)
        .args(["bootstrap", &format!("gui/{uid}")])
        .arg(plist)
        .status()
        .with_context(|| format!("running {LAUNCHCTL}"))?;
    ensure!(status.success(), "launchctl bootstrap failed ({status})");
    Ok(())
}

/// Runs `launchctl` with its output discarded and reports whether it
/// succeeded.
fn launchctl_quiet(args: &[&str]) -> bool {
    Command::new(LAUNCHCTL)
        .args(args)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}

/// Polls until `GET /api/health` on `addr` answers 200, or `timeout` passes.
fn wait_for_health(addr: SocketAddr, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if health_ok(addr) {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

/// One plain TCP request: does `GET /api/health` on `addr` answer 200? The
/// Host header is `addr` itself, which is what the server's Host check
/// admits for 127.0.0.1:7377.
fn health_ok(addr: SocketAddr) -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_secs(1)) else {
        return false;
    };
    if stream
        .set_read_timeout(Some(Duration::from_secs(2)))
        .is_err()
    {
        return false;
    }
    let request = format!("GET /api/health HTTP/1.1\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }
    let mut status_line_start = [0u8; 12];
    stream.read_exact(&mut status_line_start).is_ok() && &status_line_start == b"HTTP/1.1 200"
}

#[cfg(test)]
mod tests {
    use std::net::TcpListener;

    use tempfile::TempDir;

    use super::*;

    const APP: &str = "/Users/hatim/Library/Application Support/Respec/app";
    const CONFIG: &str = "/Users/hatim/Library/Application Support/Respec";
    const LOG: &str = "/Users/hatim/Library/Logs/Respec/respec.log";

    /// The text of every `<string>` element, in order.
    fn plist_strings(plist: &str) -> Vec<&str> {
        plist
            .split("<string>")
            .skip(1)
            .map(|rest| rest.split("</string>").next().expect("closing tag"))
            .collect()
    }

    fn render(app: &str, config: &str, log: &str) -> anyhow::Result<String> {
        render_plist(Path::new(app), Path::new(config), Path::new(log))
    }

    #[test]
    fn plist_holds_only_absolute_paths_and_restarts_the_server() {
        let plist = render(APP, CONFIG, LOG).expect("renders");
        let strings = plist_strings(&plist);
        for value in strings.iter().filter(|value| **value != LABEL) {
            assert!(value.starts_with('/'), "not absolute: {value}");
        }
        assert_eq!(strings.len(), 8, "{strings:?}");
        for expected in [
            format!("{APP}/respec"),
            APP.to_owned(),
            CONFIG.to_owned(),
            format!("{APP}/web/dist"),
            format!("{APP}/python/.venv/bin/respec-worker"),
            LOG.to_owned(),
        ] {
            assert!(strings.contains(&expected.as_str()), "missing {expected}");
        }
        for key in [
            "RESPEC_CONFIG_DIR",
            "RESPEC_WEB_DIR",
            "RESPEC_WORKER",
            "ProgramArguments",
            "WorkingDirectory",
            "StandardOutPath",
            "StandardErrorPath",
        ] {
            assert!(plist.contains(&format!("<key>{key}</key>")), "no key {key}");
        }
        assert!(plist.contains("<key>RunAtLoad</key>\n\t<true/>"));
        assert!(plist.contains("<key>KeepAlive</key>\n\t<true/>"));
        assert!(plist.contains(&format!("<string>{LABEL}</string>")));
    }

    #[test]
    fn plist_rejects_a_relative_path() {
        let cases = [
            ("app", CONFIG, LOG),
            (APP, "Respec", LOG),
            (APP, CONFIG, "Library/Logs/respec.log"),
        ];
        for (app, config, log) in cases {
            let err = render(app, config, log).expect_err("relative path rejected");
            assert!(err.to_string().contains("not an absolute path"), "{err}");
        }
    }

    #[test]
    fn plist_escapes_xml_in_paths() {
        let plist = render("/tmp/a&b<c>\"d'e/app", CONFIG, LOG).expect("renders");
        assert!(plist.contains("/tmp/a&amp;b&lt;c&gt;&quot;d&apos;e/app/respec"));
        assert!(!plist.contains("a&b"));
        assert!(!plist.contains("<c>"));
    }

    #[test]
    fn agent_files_live_under_the_given_home() {
        let home = Path::new("/Users/hatim");
        assert_eq!(
            plist_path(home),
            Path::new("/Users/hatim/Library/LaunchAgents/io.github.enhso.respec.plist")
        );
        assert_eq!(
            log_path(home),
            Path::new("/Users/hatim/Library/Logs/Respec/respec.log")
        );
    }

    /// Writes an executable file at `root/rel` and returns its path.
    fn make_exe(root: &Path, rel: &str) -> PathBuf {
        let path = root.join(rel);
        fs::create_dir_all(path.parent().expect("parent")).expect("mkdir");
        fs::write(&path, "#!/bin/sh\n").expect("write");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).expect("chmod");
        path
    }

    #[test]
    fn uv_lookup_order() {
        let tmp = TempDir::new().expect("tempdir");
        let root = tmp.path();
        let home = root.join("home");
        let from_env = make_exe(root, "env/custom-uv");
        let on_path = make_exe(root, "bin/uv");
        let local = make_exe(&home, ".local/bin/uv");
        let cargo = make_exe(&home, ".cargo/bin/uv");
        let path_var = || Some(OsString::from(root.join("bin")));

        let found = find_uv(Some(from_env.clone().into()), path_var(), &home);
        assert_eq!(found, Some(from_env.clone()), "RESPEC_UV first");

        // A RESPEC_UV that names nothing falls through to PATH.
        let missing = root.join("env/absent");
        let found = find_uv(Some(missing.clone().into()), path_var(), &home);
        assert_eq!(found, Some(on_path.clone()), "then PATH");

        fs::remove_file(&on_path).expect("remove");
        assert_eq!(find_uv(None, path_var(), &home), Some(local.clone()));

        fs::remove_file(&local).expect("remove");
        assert_eq!(find_uv(None, path_var(), &home), Some(cargo.clone()));

        fs::remove_file(&cargo).expect("remove");
        assert_eq!(find_uv(None, path_var(), &home), None);
    }

    #[test]
    fn uv_lookup_skips_files_that_are_not_executable_and_relative_path_entries() {
        let tmp = TempDir::new().expect("tempdir");
        let home = tmp.path().join("home");
        let plain = tmp.path().join("bin/uv");
        fs::create_dir_all(plain.parent().expect("parent")).expect("mkdir");
        fs::write(&plain, "not executable").expect("write");
        let path = std::env::join_paths([tmp.path().join("bin"), PathBuf::from("relative/bin")])
            .expect("join");
        assert_eq!(find_uv(None, Some(path), &home), None);
    }

    /// Serves one connection on a loopback port: reads the request, answers
    /// with `response`, and returns the address and the request it saw.
    fn serve_once(response: &'static str) -> (SocketAddr, std::thread::JoinHandle<String>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        let handle = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept");
            let mut buf = [0u8; 1024];
            let n = stream.read(&mut buf).expect("read request");
            stream.write_all(response.as_bytes()).expect("respond");
            String::from_utf8_lossy(&buf[..n]).into_owned()
        });
        (addr, handle)
    }

    #[test]
    fn health_probe_requires_a_200_and_sends_its_own_host() {
        let (addr, handle) = serve_once("HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n{}");
        assert!(health_ok(addr));
        let request = handle.join().expect("server thread");
        assert!(
            request.starts_with("GET /api/health HTTP/1.1\r\n"),
            "{request}"
        );
        assert!(request.contains(&format!("Host: {addr}\r\n")), "{request}");

        let (addr, handle) = serve_once("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
        assert!(!health_ok(addr));
        handle.join().expect("server thread");
    }

    #[test]
    fn health_probe_is_false_when_nothing_listens() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let addr = listener.local_addr().expect("addr");
        drop(listener);
        assert!(!health_ok(addr));
        assert!(!wait_for_health(addr, Duration::from_millis(300)));
    }
}
