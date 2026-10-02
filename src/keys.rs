//! Provider API keys: a key type that cannot leak by accident, and the
//! on-disk store that keeps keys in the operator's config directory.

use std::collections::BTreeMap;
use std::fmt;
use std::fs::{self, DirBuilder, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

/// The shortest key accepted. Keeps the last four characters shown for
/// display from amounting to the whole key.
const MIN_KEY_LEN: usize = 8;

/// The file inside the config directory that holds the keys.
const KEYS_FILE: &str = "keys.json";

/// A model provider Respec can hold a key for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    /// OpenRouter, serialised as `"openrouter"`.
    OpenRouter,
    /// Google Gemini, serialised as `"gemini"`.
    Gemini,
}

/// A provider API key. Its `Debug` output is redacted, and it has no
/// `Display` or `Serialize`, so a key cannot reach a log line or a response
/// by accident. Reading the value takes an explicit [`ApiKey::expose`].
#[derive(Clone, PartialEq, Eq)]
pub struct ApiKey(String);

/// A key that is too short to be a real one.
#[derive(Debug, thiserror::Error)]
#[error("a key must be at least {MIN_KEY_LEN} characters")]
pub struct KeyTooShort;

impl ApiKey {
    /// Trims `raw` and wraps it, or fails if fewer than 8 characters remain.
    pub fn new(raw: &str) -> Result<Self, KeyTooShort> {
        let trimmed = raw.trim();
        if trimmed.chars().count() < MIN_KEY_LEN {
            return Err(KeyTooShort);
        }
        Ok(Self(trimmed.to_owned()))
    }

    /// The key itself, for the one place that sends it to a provider.
    pub fn expose(&self) -> &str {
        &self.0
    }

    /// The last four characters, the only part of a key shown to the operator.
    pub fn last_four(&self) -> String {
        let mut tail: Vec<char> = self.0.chars().rev().take(4).collect();
        tail.reverse();
        tail.into_iter().collect()
    }
}

impl fmt::Debug for ApiKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ApiKey(***)")
    }
}

/// The keys held, by provider. A provider with no entry has no key set.
pub type Keys = BTreeMap<Provider, ApiKey>;

/// Why the key store could not read or write `keys.json`. Neither variant
/// carries file contents, so showing one in a log cannot leak a key.
#[derive(Debug, thiserror::Error)]
pub enum KeyStoreError {
    /// The file or its directory could not be read, created or written.
    #[error("key store I/O failed at {}: {source}", path.display())]
    Io {
        /// The path the operation failed on.
        path: PathBuf,
        /// The underlying OS error.
        source: io::Error,
    },
    /// The file is not the JSON the store writes.
    #[error("{} is not valid (line {line}, column {column})", path.display())]
    Parse {
        /// The file that failed to parse.
        path: PathBuf,
        /// Line of the first problem.
        line: usize,
        /// Column of the first problem.
        column: usize,
    },
}

/// Keys kept in `keys.json` inside a config directory. The directory is
/// created readable only by the operator (0700) and the file is written
/// owner-only (0600).
#[derive(Debug)]
pub struct KeyStore {
    dir: PathBuf,
    /// Serialises read-merge-write so two saves cannot drop each other's keys.
    write_lock: Mutex<()>,
}

impl KeyStore {
    /// A store over `dir`. Nothing is read or created until the first
    /// [`load`](Self::load) or [`save`](Self::save).
    pub fn new(dir: impl Into<PathBuf>) -> Self {
        Self {
            dir: dir.into(),
            write_lock: Mutex::new(()),
        }
    }

    /// The saved keys. A missing file means no keys.
    pub fn load(&self) -> Result<Keys, KeyStoreError> {
        let path = self.dir.join(KEYS_FILE);
        let bytes = match fs::read(&path) {
            Ok(bytes) => bytes,
            Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(Keys::new()),
            Err(source) => return Err(KeyStoreError::Io { path, source }),
        };
        let raw: BTreeMap<Provider, String> =
            serde_json::from_slice(&bytes).map_err(|err| KeyStoreError::Parse {
                path: path.clone(),
                line: err.line(),
                column: err.column(),
            })?;
        Ok(raw.into_iter().map(|(p, k)| (p, ApiKey(k))).collect())
    }

    /// Merges `updates` into the saved keys and writes the result: a temp
    /// file created 0600 in the same directory, renamed over `keys.json`.
    /// Providers absent from `updates` keep their key. Returns all keys now
    /// saved.
    pub fn save(&self, updates: Keys) -> Result<Keys, KeyStoreError> {
        let _guard = self.write_lock.lock().unwrap_or_else(|e| e.into_inner());
        let mut keys = self.load()?;
        if updates.is_empty() {
            return Ok(keys);
        }
        keys.extend(updates);
        let raw: BTreeMap<Provider, &str> = keys.iter().map(|(p, k)| (*p, k.expose())).collect();
        let json = serde_json::to_vec(&raw).expect("a map of strings serialises");
        self.write_private(&json)?;
        Ok(keys)
    }

    fn write_private(&self, contents: &[u8]) -> Result<(), KeyStoreError> {
        let io_err = |path: &Path| {
            let path = path.to_owned();
            move |source| KeyStoreError::Io { path, source }
        };
        DirBuilder::new()
            .recursive(true)
            .mode(0o700)
            .create(&self.dir)
            .map_err(io_err(&self.dir))?;
        let tmp = self.dir.join(format!("{KEYS_FILE}.tmp"));
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(io_err(&tmp))?;
        file.write_all(contents)
            .and_then(|()| file.sync_all())
            .map_err(io_err(&tmp))?;
        let target = self.dir.join(KEYS_FILE);
        fs::rename(&tmp, &target).map_err(io_err(&target))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FAKE: &str = "sk-fake-0123456789";

    #[test]
    fn key_debug_does_not_contain_the_key() {
        let key = ApiKey::new(FAKE).expect("long enough");
        assert!(!format!("{key:?}").contains(FAKE));
        assert_eq!(format!("{key:?}"), "ApiKey(***)");
        let keys: Keys = [(Provider::Gemini, key)].into();
        assert!(!format!("{keys:?}").contains(FAKE));
    }

    #[test]
    fn short_key_is_refused_and_value_is_trimmed() {
        assert!(ApiKey::new("1234567").is_err());
        assert!(ApiKey::new("  1234567  ").is_err());
        let key = ApiKey::new("  abcdefgh  ").expect("8 characters");
        assert_eq!(key.expose(), "abcdefgh");
        assert_eq!(key.last_four(), "efgh");
    }

    #[test]
    fn missing_file_means_no_keys() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let store = KeyStore::new(tmp.path().join("absent"));
        assert!(store.load().expect("load").is_empty());
    }

    #[test]
    fn save_merges_into_existing_keys() {
        let tmp = tempfile::tempdir().expect("tempdir");
        let store = KeyStore::new(tmp.path());
        let key = |s| ApiKey::new(s).expect("long enough");
        store
            .save([(Provider::OpenRouter, key("openrouter-key"))].into())
            .expect("first save");
        let merged = store
            .save([(Provider::Gemini, key("gemini-key-1"))].into())
            .expect("second save");
        assert_eq!(merged.len(), 2);
        let loaded = store.load().expect("load");
        assert_eq!(loaded[&Provider::OpenRouter].expose(), "openrouter-key");
        assert_eq!(loaded[&Provider::Gemini].expose(), "gemini-key-1");
    }

    #[test]
    fn corrupt_file_error_carries_no_contents() {
        let tmp = tempfile::tempdir().expect("tempdir");
        fs::write(
            tmp.path().join(KEYS_FILE),
            r#"{"openrouter": "sk-leaky-0123", oops"#,
        )
        .expect("write");
        let err = KeyStore::new(tmp.path())
            .load()
            .expect_err("must not parse");
        assert!(matches!(err, KeyStoreError::Parse { .. }));
        assert!(!err.to_string().contains("sk-leaky"));
    }
}
