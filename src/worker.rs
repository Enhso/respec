//! The Python worker: one subprocess per job. This module holds the progress
//! messages it prints and the way the server starts it and reads them.

use std::io;
use std::path::PathBuf;
use std::process::{ExitStatus, Stdio};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, ChildStdout, Command};

use crate::keys::{ApiKey, Provider};

/// How long one test call may take, start to exit.
const TEST_CALL_TIMEOUT: Duration = Duration::from_secs(120);

/// How many characters of an unparseable worker line a log entry may hold.
const LOGGED_LINE_CHARS: usize = 200;

/// The environment variable the worker reads each Provider's key from.
const KEY_VARS: [(Provider, &str); 2] = [
    (Provider::OpenRouter, "OPENROUTER_API_KEY"),
    (Provider::Gemini, "GEMINI_API_KEY"),
];

/// One progress message: a JSON line on the worker's stdout, told apart by
/// its `kind`. The shapes are the contract in `contracts/fixtures/messages/`,
/// which the Python worker's own models mirror.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum WorkerMessage {
    /// The worker is about to call the Model.
    Started {
        /// The Provider being called.
        provider: Provider,
        /// The Model being called.
        model: String,
    },
    /// The call succeeded.
    Done {
        /// The Provider that answered.
        provider: Provider,
        /// The Model that answered.
        model: String,
        /// The Model's reply text.
        reply: String,
    },
    /// The call failed.
    Failed {
        /// The Provider that was called.
        provider: Provider,
        /// Why it failed.
        reason: FailureReason,
        /// One plain sentence with a next step, safe to show the operator.
        message: String,
    },
}

/// Why a call failed, in terms the operator can act on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureReason {
    /// The Provider rejected the key.
    Auth,
    /// The account is out of credit or quota.
    Quota,
    /// A rate limit was reached; trying later may work.
    RateLimit,
    /// The Provider does not offer the Model.
    ModelUnavailable,
    /// The Provider could not be reached.
    Network,
    /// Anything else.
    Other,
}

/// How a worker run ended, as its last `done` or `failed` message says.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Finished {
    /// The call succeeded.
    Done {
        /// The Provider that answered.
        provider: Provider,
        /// The Model that answered.
        model: String,
        /// The Model's reply text.
        reply: String,
    },
    /// The call failed.
    Failed {
        /// The Provider that was called.
        provider: Provider,
        /// Why it failed.
        reason: FailureReason,
        /// One plain sentence with a next step.
        message: String,
    },
}

/// Why a worker run produced no `done` or `failed` message. None of these
/// carry a key, the worker's environment or its output.
#[derive(Debug, thiserror::Error)]
pub enum WorkerError {
    /// The worker executable could not be started.
    #[error("could not start the worker at {}: {source}", path.display())]
    Spawn {
        /// The executable the server tried to run.
        path: PathBuf,
        /// The underlying OS error.
        source: io::Error,
    },
    /// Reading the worker's output or waiting for it failed.
    #[error("reading from the worker failed: {0}")]
    Read(io::Error),
    /// The worker did not exit in time and was killed.
    #[error("the worker did not finish within {0:?}")]
    TimedOut(Duration),
    /// The worker exited without printing a `done` or `failed` message.
    #[error("the worker exited ({status}) without a final message")]
    NoFinalMessage {
        /// How the worker exited.
        status: ExitStatus,
    },
}

/// The worker executable and how long a run may take.
#[derive(Debug, Clone)]
pub struct Worker {
    path: PathBuf,
    timeout: Duration,
}

impl Worker {
    /// A worker at `path`, with the standard 120 s limit.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            timeout: TEST_CALL_TIMEOUT,
        }
    }

    /// The same worker with a different time limit.
    #[must_use]
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = timeout;
        self
    }

    /// Runs `test-call` for `provider`. The key reaches the worker only
    /// through its environment: both key variables are cleared first, so a
    /// key set in the server's own environment is not passed on. The worker
    /// is killed if this future is dropped or the time limit passes.
    pub async fn test_call(
        &self,
        provider: Provider,
        key: &ApiKey,
    ) -> Result<Finished, WorkerError> {
        let mut command = Command::new(&self.path);
        command.args(["test-call", "--provider", provider.as_str()]);
        for (_, var) in KEY_VARS {
            command.env_remove(var);
        }
        if let Some((_, var)) = KEY_VARS.iter().find(|(p, _)| *p == provider) {
            command.env(var, key.expose());
        }
        let mut child = command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .kill_on_drop(true)
            .spawn()
            .map_err(|source| WorkerError::Spawn {
                path: self.path.clone(),
                source,
            })?;
        let stdout = child.stdout.take().expect("stdout is piped");
        tokio::time::timeout(self.timeout, read_messages(&mut child, stdout))
            .await
            .map_err(|_| WorkerError::TimedOut(self.timeout))?
    }
}

/// Reads the worker's stdout to its end, keeping the last `done` or `failed`
/// message, then waits for it to exit. A line that is not a message is logged
/// (cut short) and skipped.
async fn read_messages(child: &mut Child, stdout: ChildStdout) -> Result<Finished, WorkerError> {
    let mut lines = BufReader::new(stdout).lines();
    let mut finished = None;
    while let Some(line) = lines.next_line().await.map_err(WorkerError::Read)? {
        match serde_json::from_str::<WorkerMessage>(&line) {
            Ok(WorkerMessage::Started { .. }) => {}
            Ok(WorkerMessage::Done {
                provider,
                model,
                reply,
            }) => {
                finished = Some(Finished::Done {
                    provider,
                    model,
                    reply,
                });
            }
            Ok(WorkerMessage::Failed {
                provider,
                reason,
                message,
            }) => {
                finished = Some(Finished::Failed {
                    provider,
                    reason,
                    message,
                });
            }
            Err(err) => {
                let line: String = line.chars().take(LOGGED_LINE_CHARS).collect();
                tracing::warn!(%err, ?line, "worker printed a line that is not a message");
            }
        }
    }
    let status = child.wait().await.map_err(WorkerError::Read)?;
    finished.ok_or(WorkerError::NoFinalMessage { status })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;

    /// ISC-17, server side: every fixture in `contracts/fixtures/messages/`
    /// parses as a [`WorkerMessage`], and writing it back gives the same JSON,
    /// so a field the enum lacks or invents shows up here.
    #[test]
    fn contract_fixtures_parse() {
        let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("contracts/fixtures/messages");
        let mut files: Vec<_> = fs::read_dir(&dir)
            .unwrap_or_else(|err| panic!("reading {}: {err}", dir.display()))
            .map(|entry| entry.expect("directory entry").path())
            .filter(|path| path.extension().is_some_and(|ext| ext == "json"))
            .collect();
        files.sort();
        assert!(!files.is_empty(), "no fixtures in {}", dir.display());

        for path in files {
            let text = fs::read_to_string(&path).expect("fixture is readable");
            let raw: serde_json::Value = serde_json::from_str(&text).expect("fixture is JSON");
            let message: WorkerMessage = serde_json::from_str(&text)
                .unwrap_or_else(|err| panic!("{} is not a WorkerMessage: {err}", path.display()));
            assert_eq!(
                serde_json::to_value(&message).expect("serialises"),
                raw,
                "{} does not round-trip",
                path.display()
            );
        }
    }
}
