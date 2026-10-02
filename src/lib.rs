//! Respec's server: the only process that opens the mnestic store. It owns
//! the domain invariants, the HTTP API and the job runner, and serves the
//! built web UI from the same origin.

pub mod keys;
pub mod server;
pub mod store;
