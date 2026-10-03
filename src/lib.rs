//! Respec's server: the only process that opens the mnestic store. It owns
//! the domain invariants, the HTTP API and the job runner, and serves the
//! built web UI from the same origin.

use std::ffi::OsString;
use std::path::PathBuf;

use anyhow::Context;

pub mod keys;
pub mod server;
pub mod setup;
pub mod store;
pub mod test_extraction;
pub mod worker;

/// The directory holding keys: `RESPEC_CONFIG_DIR` if set, else `Respec`
/// inside the platform config directory (`~/Library/Application Support` on
/// macOS). The server and `respec setup` both resolve it here.
pub fn config_dir() -> anyhow::Result<PathBuf> {
    config_dir_from(std::env::var_os("RESPEC_CONFIG_DIR"), dirs::config_dir())
}

/// [`config_dir`] with its two inputs passed in, so tests need not touch the
/// process environment.
fn config_dir_from(
    override_dir: Option<OsString>,
    platform_dir: Option<PathBuf>,
) -> anyhow::Result<PathBuf> {
    if let Some(dir) = override_dir {
        return Ok(dir.into());
    }
    let base = platform_dir.context("no platform config directory; set RESPEC_CONFIG_DIR")?;
    Ok(base.join("Respec"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn override_wins_over_the_platform_directory() {
        let dir = config_dir_from(Some("/custom".into()), Some("/platform".into()));
        assert_eq!(dir.expect("resolves"), PathBuf::from("/custom"));
    }

    #[test]
    fn default_is_respec_inside_the_platform_directory() {
        let dir = config_dir_from(None, Some("/Users/x/Library/Application Support".into()));
        assert_eq!(
            dir.expect("resolves"),
            PathBuf::from("/Users/x/Library/Application Support/Respec")
        );
    }

    #[test]
    fn no_override_and_no_platform_directory_is_an_error() {
        assert!(config_dir_from(None, None).is_err());
    }
}
