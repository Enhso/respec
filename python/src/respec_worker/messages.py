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
"""What a running extraction is doing."""


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
    """An extraction is working; ``detail`` is one plain sentence.

    While a pass of ``extract`` runs, ``pass_number`` and ``pass_count`` say
    which pass it is ("pass 1 of 2"). Both are null for fetching and for the
    single call of ``test-extraction``.
    """

    kind: Literal["progress"] = "progress"
    provider: Provider
    stage: Stage
    detail: str
    pass_number: int | None = None
    pass_count: int | None = None


class DocumentSummary(_Message):
    """The Document an extraction read: where from, its title, its size.

    Both ``url`` and ``title`` are null for Document text read from a file.
    """

    url: str | None
    title: str | None
    chars: int
    """The length of the Document text, in characters."""


class ProposedEntity(_Message):
    """One entity Proposal: its id, kind, name and first supporting sentence.

    ``id`` is the short id the worker stamped (``e1``, ``e2``, ...); Pass 2
    refers to the entity by it. ``date`` (ISO 8601, which may stop at the year
    or the month) and ``place`` belong to an Event and are null for every other
    kind, and for an Event whose article does not say.
    """

    id: str
    label: str
    name: str
    sentence: str
    date: str | None = None
    place: str | None = None


class Entities(_Message):
    """The entity Proposals ``model`` made. It ends a ``test-extraction`` run;
    in an ``extract`` run it follows Pass 1 and ``Relationships`` ends the run.

    ``dropped`` counts the items of the Model's reply that were left out for
    being malformed.
    """

    kind: Literal["entities"] = "entities"
    provider: Provider
    model: str
    document: DocumentSummary
    entities: list[ProposedEntity]
    dropped: int


class ProposedRelationship(_Message):
    """One relationship Proposal between two entities, named by their ids."""

    type: str
    from_id: str
    to_id: str
    date_from: str | None
    date_to: str | None
    date_precision: str
    sentence: str
    """The Proposal's first supporting sentence."""


class Relationships(_Message):
    """An ``extract`` run succeeded: the relationship Proposals ``model`` made.

    ``dropped`` counts the items of the Model's reply that were left out: those
    that were malformed, those that named an id Pass 1 never gave, and
    participant links that did not go from an entity to an Event.
    """

    kind: Literal["relationships"] = "relationships"
    provider: Provider
    model: str
    relationships: list[ProposedRelationship]
    dropped: int


class Failed(_Message):
    """The run failed. ``message`` is one plain sentence with a next step."""

    kind: Literal["failed"] = "failed"
    provider: Provider
    reason: Reason
    message: str


AnyMessage = Started | Progress | Done | Entities | Relationships | Failed
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
