"""The worker's progress messages: JSON Lines on stdout, one object per line.

The shapes here are the worker side of the contract in
``contracts/fixtures/messages/``. The Rust server parses the same lines into
its own ``WorkerMessage`` enum. Messages are discriminated by ``kind``.
"""

import sys
from typing import Annotated, Literal

import orjson
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from respec_worker.providers import Provider

Reason = Literal[
    "auth",
    "quota",
    "rate_limit",
    "model_unavailable",
    "network",
    "fetch",
    "bad_output",
    "other",
]
"""Why a run failed, in terms the operator can act on."""

Stage = Literal["fetching", "calling_model", "waiting_rate_limit"]
"""What a running test extraction is doing."""


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Started(_Message):
    """The worker is about to call ``model`` at ``provider``."""

    kind: Literal["started"] = "started"
    provider: Provider
    model: str


class Done(_Message):
    """The call succeeded and ``reply`` is the Model's answer."""

    kind: Literal["done"] = "done"
    provider: Provider
    model: str
    reply: str


class Progress(_Message):
    """A test extraction is working; ``detail`` is one plain sentence."""

    kind: Literal["progress"] = "progress"
    provider: Provider
    stage: Stage
    detail: str


class DocumentSummary(_Message):
    """The Document a test extraction read: where from, its title, its size."""

    url: str
    title: str | None
    chars: int
    """The length of the Document text, in characters."""


class ProposedEntity(_Message):
    """One entity Proposal: its kind, its name and its first supporting sentence."""

    label: str
    name: str
    sentence: str


class Entities(_Message):
    """A test extraction succeeded: the entity Proposals ``model`` made."""

    kind: Literal["entities"] = "entities"
    provider: Provider
    model: str
    document: DocumentSummary
    entities: list[ProposedEntity]


class Failed(_Message):
    """The run failed. ``message`` is one plain sentence with a next step."""

    kind: Literal["failed"] = "failed"
    provider: Provider
    reason: Reason
    message: str


AnyMessage = Started | Progress | Done | Entities | Failed
"""Any one worker message."""

Message = Annotated[AnyMessage, Field(discriminator="kind")]

MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)
"""Validates one parsed or raw JSON message into the right model."""


def emit(message: AnyMessage) -> None:
    """Write ``message`` to stdout as one UTF-8 JSON line and flush.

    The bytes go straight to the buffer, because article text holds non-ASCII
    names and the text layer's encoding follows the operator's locale.
    """
    sys.stdout.buffer.write(orjson.dumps(message.model_dump(mode="json")) + b"\n")
    sys.stdout.flush()
