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

/// How long one test extraction may take, start to exit. It covers an article
/// fetch and a free Model that can take minutes, and waits on rate limits.
const TEST_EXTRACTION_TIMEOUT: Duration = Duration::from_secs(20 * 60);

/// The environment variable the worker reads each Provider's key from.
const KEY_VARS: [(Provider, &str); 2] = [
    (Provider::OpenRouter, "OPENROUTER_API_KEY"),
    (Provider::Gemini, "GEMINI_API_KEY"),
];

/// One progress message: a JSON line on the worker's stdout, told apart by
/// its `kind`. The shapes are the contract in `contracts/fixtures/messages/`,
/// which the Python worker's own models mirror. `Done`, `Entities` and
/// `Failed` end a run; the others report on the way.
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
    /// A test extraction is working.
    Progress {
        /// The Provider being called.
        provider: Provider,
        /// What the worker is doing.
        stage: Stage,
        /// One plain sentence saying so.
        detail: String,
    },
    /// The test call succeeded.
    Done {
        /// The Provider that answered.
        provider: Provider,
        /// The Model that answered.
        model: String,
        /// The Model's reply text.
        reply: String,
    },
    /// The test extraction succeeded.
    Entities {
        /// The Provider that answered.
        provider: Provider,
        /// The Model that answered.
        model: String,
        /// The Document the entity Proposals come from.
        document: DocumentSummary,
        /// The entity Proposals, in the Model's order.
        entities: Vec<ProposedEntity>,
    },
    /// The run failed.
    Failed {
        /// The Provider that was called.
        provider: Provider,
        /// Why it failed.
        reason: FailureReason,
        /// One plain sentence with a next step, safe to show the operator.
        message: String,
    },
}

impl WorkerMessage {
    /// Whether this message ends a run: the last one of these is its outcome.
    fn is_final(&self) -> bool {
        matches!(
            self,
            Self::Done { .. } | Self::Entities { .. } | Self::Failed { .. }
        )
    }
}

/// What a running test extraction is doing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    /// Fetching the article.
    Fetching,
    /// Waiting for the Model's reply.
    CallingModel,
    /// Waiting out a per-minute rate limit before trying again.
    WaitingRateLimit,
}

/// The Document a test extraction read.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DocumentSummary {
    /// Where the article was found, after redirects.
    pub url: String,
    /// The article's title, when the page has one.
    pub title: Option<String>,
    /// The length of the Document text, in characters.
    pub chars: u64,
}

/// One entity Proposal as the worker reports it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProposedEntity {
    /// The kind of entity: Person, Organization and so on.
    pub label: String,
    /// The entity's name.
    pub name: String,
    /// The Proposal's first supporting sentence.
    pub sentence: String,
}

/// Why a run failed, in terms the operator can act on.
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
    /// The article could not be fetched, or had no body.
    Fetch,
    /// The Model's reply was not valid Pass 1 JSON.
    BadOutput,
    /// Anything else.
    Other,
}

/// Why a worker run produced no final message. None of these carry a key,
/// the worker's environment or its output.
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
    /// The worker exited without printing a `done`, `entities` or `failed`
    /// message.
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
    timeout: Option<Duration>,
}

impl Worker {
    /// A worker at `path`. A test call may take 120 s and a test extraction
    /// 20 minutes.
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            timeout: None,
        }
    }

    /// The same worker with one time limit for every kind of run.
    #[must_use]
    pub fn with_timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    /// Runs `test-call` for `provider` and returns its final message.
    pub async fn test_call(
        &self,
        provider: Provider,
        key: &ApiKey,
    ) -> Result<WorkerMessage, WorkerError> {
        let args = ["test-call", "--provider", provider.as_str()];
        let timeout = self.timeout.unwrap_or(TEST_CALL_TIMEOUT);
        self.run(provider, key, &args, timeout, |_| {}).await
    }

    /// Runs `test-extraction` over the article at `url` and returns its final
    /// message. `on_message` sees every message as it arrives, so progress can
    /// be shown while the run goes on.
    pub async fn test_extraction(
        &self,
        provider: Provider,
        key: &ApiKey,
        url: &str,
        on_message: impl FnMut(&WorkerMessage),
    ) -> Result<WorkerMessage, WorkerError> {
        let args = [
            "test-extraction",
            "--provider",
            provider.as_str(),
            "--url",
            url,
        ];
        let timeout = self.timeout.unwrap_or(TEST_EXTRACTION_TIMEOUT);
        self.run(provider, key, &args, timeout, on_message).await
    }

    /// Starts the worker with `args` and reads its messages until it exits,
    /// returning the last final one. The key reaches the worker only through
    /// its environment: both key variables are cleared first, so a key set in
    /// the server's own environment is not passed on. The worker is killed if
    /// this future is dropped or `timeout` passes.
    async fn run(
        &self,
        provider: Provider,
        key: &ApiKey,
        args: &[&str],
        timeout: Duration,
        mut on_message: impl FnMut(&WorkerMessage),
    ) -> Result<WorkerMessage, WorkerError> {
        let mut command = Command::new(&self.path);
        command.args(args);
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
        tokio::time::timeout(timeout, read_messages(&mut child, stdout, &mut on_message))
            .await
            .map_err(|_| WorkerError::TimedOut(timeout))?
    }
}

/// Reads the worker's stdout to its end, handing each message to
/// `on_message` and keeping the last final one, then waits for the worker to
/// exit. A line that is not a message is skipped; the log gets the parse
/// error and the line's length, never its text, which may be article content.
async fn read_messages(
    child: &mut Child,
    stdout: ChildStdout,
    on_message: &mut impl FnMut(&WorkerMessage),
) -> Result<WorkerMessage, WorkerError> {
    let mut lines = BufReader::new(stdout).lines();
    let mut outcome = None;
    while let Some(line) = lines.next_line().await.map_err(WorkerError::Read)? {
        match serde_json::from_str::<WorkerMessage>(&line) {
            Ok(message) => {
                on_message(&message);
                if message.is_final() {
                    outcome = Some(message);
                }
            }
            Err(err) => {
                let chars = line.chars().count();
                tracing::warn!(%err, chars, "worker printed a line that is not a message");
            }
        }
    }
    let status = child.wait().await.map_err(WorkerError::Read)?;
    outcome.ok_or(WorkerError::NoFinalMessage { status })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeSet;
    use std::fs;
    use std::path::Path;

    /// ISC-17, server side: every fixture in `contracts/fixtures/messages/`
    /// parses as a [`WorkerMessage`], and writing it back gives the same JSON,
    /// so a field the enum lacks or invents shows up here. Every message
    /// kind, failure reason and progress stage has a fixture.
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

        let mut kinds = BTreeSet::new();
        let mut reasons = BTreeSet::new();
        let mut stages = BTreeSet::new();
        for path in files {
            let text = fs::read_to_string(&path).expect("fixture is readable");
            let raw: serde_json::Value = serde_json::from_str(&text).expect("fixture is JSON");
            for (set, field) in [
                (&mut kinds, "kind"),
                (&mut reasons, "reason"),
                (&mut stages, "stage"),
            ] {
                if let Some(value) = raw[field].as_str() {
                    set.insert(value.to_owned());
                }
            }
            let message: WorkerMessage = serde_json::from_str(&text)
                .unwrap_or_else(|err| panic!("{} is not a WorkerMessage: {err}", path.display()));
            assert_eq!(
                serde_json::to_value(&message).expect("serialises"),
                raw,
                "{} does not round-trip",
                path.display()
            );
        }

        let names = |names: &[&str]| -> BTreeSet<String> {
            names.iter().map(|&name| name.to_owned()).collect()
        };
        assert_eq!(
            kinds,
            names(&["started", "progress", "done", "entities", "failed"])
        );
        assert_eq!(
            reasons,
            names(&[
                "auth",
                "quota",
                "rate_limit",
                "model_unavailable",
                "network",
                "fetch",
                "bad_output",
                "other"
            ])
        );
        assert_eq!(
            stages,
            names(&["fetching", "calling_model", "waiting_rate_limit"])
        );
    }
}
