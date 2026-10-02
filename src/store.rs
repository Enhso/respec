//! The mnestic store. Only this process opens it, and it is an index rebuilt
//! from the plain files in the data directory, never the truth.

use std::collections::BTreeMap;
use std::path::Path;

use cozo::{DbInstance, ScriptMutability};

/// A handle to an open mnestic database.
pub struct Store {
    db: DbInstance,
}

impl std::fmt::Debug for Store {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Store").finish_non_exhaustive()
    }
}

/// An error returned by a [`Store`] operation.
#[derive(Debug, thiserror::Error)]
pub enum StoreError {
    /// mnestic failed. The message is its formatted report.
    #[error("store error: {0}")]
    Db(String),
    /// A query returned a row of a shape its reader did not expect.
    #[error("unexpected row shape in {query}: {detail}")]
    RowShape { query: &'static str, detail: String },
}

impl Store {
    /// Opens an empty store held in memory.
    ///
    /// # Errors
    /// Returns [`StoreError::Db`] if mnestic fails to open it.
    pub fn open_memory() -> Result<Self, StoreError> {
        let db = DbInstance::new("mem", "", "").map_err(db_error)?;
        Ok(Self { db })
    }

    /// Opens, or creates, a store persisted at `path` on mnestic's sqlite
    /// engine.
    ///
    /// # Errors
    /// Returns [`StoreError::Db`] if mnestic fails to open it.
    pub fn open_sqlite(path: &Path) -> Result<Self, StoreError> {
        let db = DbInstance::new("sqlite", path, "").map_err(db_error)?;
        Ok(Self { db })
    }

    /// Lists the names of the relations in the store.
    ///
    /// # Errors
    /// Returns [`StoreError::Db`] if the query fails, or
    /// [`StoreError::RowShape`] if a row has no name.
    pub fn relations(&self) -> Result<Vec<String>, StoreError> {
        let rows = self
            .db
            .run_script("::relations", BTreeMap::new(), ScriptMutability::Immutable)
            .map_err(db_error)?;
        rows.rows
            .iter()
            .map(|row| {
                row.first()
                    .and_then(|name| name.get_str())
                    .map(str::to_owned)
                    .ok_or_else(|| StoreError::RowShape {
                        query: "::relations",
                        detail: format!("{row:?}"),
                    })
            })
            .collect()
    }
}

fn db_error(err: impl std::fmt::Debug) -> StoreError {
    StoreError::Db(format!("{err:?}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_probe(store: &Store) {
        store
            .db
            .run_script(
                ":create probe {id: String}",
                BTreeMap::new(),
                ScriptMutability::Mutable,
            )
            .expect("create relation");
    }

    #[test]
    fn memory_store_starts_empty() {
        let store = Store::open_memory().expect("open memory store");
        assert!(store.relations().expect("list relations").is_empty());
    }

    #[test]
    fn sqlite_store_persists_across_reopen() {
        let dir = tempfile::tempdir().expect("create tempdir");
        let path = dir.path().join("respec.db");

        create_probe(&Store::open_sqlite(&path).expect("open sqlite store"));

        let reopened = Store::open_sqlite(&path).expect("reopen sqlite store");
        assert_eq!(reopened.relations().expect("list relations"), ["probe"]);
    }
}
