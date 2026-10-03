//! The state of the test extraction: the current or last run, kept in memory
//! for the page to poll. One run at a time, and nothing persists.

use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::Instant;

use serde::Serialize;
use serde_json::{Value, json};

use crate::keys::Provider;
use crate::worker::{
    DocumentSummary, FailureReason, ProposedEntity, Stage, WorkerError, WorkerMessage,
};

/// Where a run is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
enum Status {
    Running,
    Done,
    Failed,
}

/// One progress message the worker sent.
#[derive(Serialize)]
struct Progress {
    stage: Stage,
    detail: String,
}

/// The entity Proposals of a run that finished.
#[derive(Serialize)]
struct Outcome {
    model: String,
    document: DocumentSummary,
    entities: Vec<ProposedEntity>,
}

/// Why a run failed, in a sentence the operator can read.
#[derive(Serialize)]
struct Failure {
    reason: FailureReason,
    message: String,
}

/// One test extraction. It serialises as the body of
/// `GET /api/test-extraction`: `result` is present once it is done and
/// `failure` once it has failed.
#[derive(Serialize)]
struct Run {
    status: Status,
    provider: Provider,
    url: String,
    elapsed_secs: u64,
    progress: Vec<Progress>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<Outcome>,
    #[serde(skip_serializing_if = "Option::is_none")]
    failure: Option<Failure>,
    #[serde(skip)]
    started: Instant,
}

/// The current or last test extraction, shared between the handlers and the
/// task that runs the worker.
#[derive(Default)]
pub struct TestExtractions {
    run: Mutex<Option<Run>>,
}

impl TestExtractions {
    fn run(&self) -> MutexGuard<'_, Option<Run>> {
        self.run.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Records a new run as started, unless one is already running, and says
    /// whether it did. The check and the change are one step.
    pub fn start(&self, provider: Provider, url: &str) -> bool {
        let mut slot = self.run();
        if slot
            .as_ref()
            .is_some_and(|run| run.status == Status::Running)
        {
            return false;
        }
        *slot = Some(Run {
            status: Status::Running,
            provider,
            url: url.to_owned(),
            elapsed_secs: 0,
            progress: Vec::new(),
            result: None,
            failure: None,
            started: Instant::now(),
        });
        true
    }

    /// Keeps a progress message of the running run; other messages are not
    /// progress and are ignored.
    pub fn record(&self, message: &WorkerMessage) {
        if let (Some(run), WorkerMessage::Progress { stage, detail, .. }) =
            (self.run().as_mut(), message)
        {
            run.progress.push(Progress {
                stage: *stage,
                detail: detail.clone(),
            });
        }
    }

    /// Ends the running run with the way the worker run ended. A run that
    /// produced no usable final message fails with a plain sentence; the
    /// cause goes to the log. Never logs the worker's output.
    pub fn finish(&self, ended: Result<WorkerMessage, WorkerError>) {
        let mut slot = self.run();
        let Some(run) = slot.as_mut() else { return };
        run.elapsed_secs = run.started.elapsed().as_secs();
        match ended {
            Ok(WorkerMessage::Entities {
                model,
                document,
                entities,
                ..
            }) => {
                tracing::info!(provider = ?run.provider, %model, count = entities.len(), "test extraction finished");
                run.status = Status::Done;
                run.result = Some(Outcome {
                    model,
                    document,
                    entities,
                });
            }
            Ok(WorkerMessage::Failed {
                reason, message, ..
            }) => {
                tracing::info!(provider = ?run.provider, ?reason, "test extraction failed");
                run.status = Status::Failed;
                run.failure = Some(Failure { reason, message });
            }
            Ok(_) => {
                tracing::error!(provider = ?run.provider, "test extraction ended with an unexpected message");
                run.fail_with(
                    "The worker gave an answer Respec did not expect; see the server log.",
                );
            }
            Err(err) => {
                tracing::error!(%err, provider = ?run.provider, "test extraction failed");
                let message = match err {
                    WorkerError::TimedOut(_) => {
                        "The test extraction took too long and was stopped; try again \
                         later, or with a shorter article."
                    }
                    WorkerError::NoFinalMessage { .. } => {
                        "The worker stopped without giving a result; see the server log."
                    }
                    WorkerError::Spawn { .. } | WorkerError::Read(_) => {
                        "The worker could not be run; see the server log."
                    }
                };
                run.fail_with(message);
            }
        }
    }

    /// The run as JSON for the page, or `{"status": "idle"}` if none has run.
    pub fn snapshot(&self) -> Value {
        let mut slot = self.run();
        let Some(run) = slot.as_mut() else {
            return json!({ "status": "idle" });
        };
        if run.status == Status::Running {
            run.elapsed_secs = run.started.elapsed().as_secs();
        }
        serde_json::to_value(&*run).expect("a run serialises")
    }
}

impl Run {
    fn fail_with(&mut self, message: &str) {
        self.status = Status::Failed;
        self.failure = Some(Failure {
            reason: FailureReason::Other,
            message: message.to_owned(),
        });
    }
}
