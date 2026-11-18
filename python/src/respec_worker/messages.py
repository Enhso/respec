"""The worker's progress messages: JSON Lines on stdout, one object per line.

The shapes here are the worker side of the contract in
``contracts/fixtures/messages/``. The Rust server parses the same lines into
its own ``WorkerMessage`` enum. Messages are discriminated by ``kind``.
"""

import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Literal, NamedTuple

import orjson
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from respec_worker.providers import PROVIDERS, Provider

Reason = Literal[
    "auth",
    "quota",
    "daily_quota",
    "rate_limit",
    "overloaded",
    "model_unavailable",
    "network",
    "fetch",
    "bad_output",
    "output_truncated",
    "other",
]
"""Why a run failed, in terms the operator can act on."""

Stage = Literal[
    "fetching",
    "calling_model",
    "waiting_rate_limit",
    "waiting_overloaded",
    "falling_back",
]
"""What a running extraction is doing."""


def describe_reset(retry_at: datetime | None, now: datetime) -> str:
    """When a quota comes back, in words: the UTC time and how far away it is.

    Without a ``retry_at`` the Provider did not say, so only the usual daily
    reset is named.
    """
    if retry_at is None:
        return "once the quota resets, which is usually within a day"
    seconds = (retry_at - now).total_seconds()
    if seconds < 3600:
        away = "under an hour"
    else:
        hours = round(seconds / 3600)
        away = f"about {hours} hour{'s' if hours > 1 else ''}"
    return f"after {retry_at.astimezone(UTC):%Y-%m-%d %H:%M} UTC (in {away})"


class FailureText(NamedTuple):
    """The two halves of a failure message: what happened, and the next step.

    Both may use ``{provider}`` (the Provider's name) and ``{model}``; the next
    step may also use ``{when}`` (see ``describe_reset``).
    """

    what: str
    next_step: str


FAILURE_TEXT: dict[Reason, FailureText] = {
    "auth": FailureText(
        "{provider} rejected the key",
        "check the key in Respec's settings and save it again",
    ),
    "quota": FailureText(
        "{provider} says the account is out of credit",
        "check the account's billing or quota, then try again",
    ),
    "daily_quota": FailureText(
        "{provider} has used up its free daily quota for {model}",
        "try again {when}",
    ),
    "rate_limit": FailureText(
        "The {provider} rate limit was reached", "try again in a few minutes"
    ),
    "overloaded": FailureText(
        "{provider} is overloaded right now",
        "try again in a few minutes, or later if it stays busy",
    ),
    "model_unavailable": FailureText(
        "{provider} is not offering the model {model} to this account",
        "try again later, or choose another model once that is possible",
    ),
    "network": FailureText(
        "Could not reach {provider}", "check the internet connection and try again"
    ),
    "fetch": FailureText(
        "The article could not be read", "check the address or the file, then try again"
    ),
    "bad_output": FailureText(
        "The Model's reply was not in the form Respec needs",
        "try again, or later with another Model",
    ),
    "output_truncated": FailureText(
        "The Model's reply was cut off at its output limit",
        "try a shorter article, or later with another Model",
    ),
    "other": FailureText(
        "{provider} gave an answer Respec could not use", "try again later"
    ),
}
"""One plain message for each failure ``Reason``, with a next step (ISC-53). A
test walks every ``Reason``, so a new one without an entry fails. The messages
of a failed fetch, a bad reply and a missing key keep their own, more specific
sentences at the place that raises them."""


def _fields(
    provider: Provider, model: str, retry_at: datetime | None, now: datetime | None
) -> dict[str, str]:
    return {
        "provider": PROVIDERS[provider].label,
        "model": model,
        "when": describe_reset(retry_at, now or datetime.now(UTC)),
    }


def failure_what(reason: Reason, provider: Provider, model: str) -> str:
    """What happened, in a few words, when ``model`` at ``provider`` failed with
    ``reason``: the first half of ``failure_message``."""
    return FAILURE_TEXT[reason].what.format_map(_fields(provider, model, None, None))


def failure_message(
    reason: Reason,
    provider: Provider,
    model: str,
    *,
    retry_at: datetime | None = None,
    now: datetime | None = None,
) -> str:
    """The plain sentence for ``reason``, with its next step, about ``model``
    at ``provider``. ``retry_at`` is when a ``daily_quota`` ends, if known."""
    text = FAILURE_TEXT[reason]
    sentence = f"{text.what}; {text.next_step}."
    return sentence.format_map(_fields(provider, model, retry_at, now))


def failure_summary(
    tried: Sequence[tuple[Provider, str, Reason]],
    *,
    retry_at: datetime | None = None,
    now: datetime | None = None,
) -> str:
    """The message of a run in which every Model in ``tried`` (provider, model
    and reason, in the order tried) failed: what happened to each, then the
    next step for the last. ``retry_at`` is when a daily quota ends, if known."""
    last_provider, last_model, last_reason = tried[-1]
    each = " ".join(
        f"{model}: {failure_what(reason, provider, model)}."
        for provider, model, reason in tried
    )
    step = FAILURE_TEXT[last_reason].next_step.format_map(
        _fields(last_provider, last_model, retry_at, now)
    )
    return f"No Model could finish this article. {each} {step[0].upper()}{step[1:]}."


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
    refers to the entity by it. ``sentence`` is the Document text's own slice
    ``text[sentence_start:sentence_end]``; the offsets count Unicode code
    points, with the end exclusive. ``date`` (ISO 8601, which may stop at the
    year or the month) and ``place`` belong to an Event and are null for every
    other kind, and for an Event whose article does not say.
    """

    id: str
    label: str
    name: str
    sentence: str
    sentence_start: int
    sentence_end: int
    date: str | None = None
    place: str | None = None


class Entities(_Message):
    """The entity Proposals ``model`` made. It ends a ``test-extraction`` run;
    in an ``extract`` run it follows Pass 1 and ``Relationships`` ends the run.

    ``dropped`` counts the Proposals left out: items of the Model's reply that
    were malformed, and those with no supporting sentence found in the Document
    text. ``sentences_dropped`` counts the supporting sentences that were not
    found in it.
    """

    kind: Literal["entities"] = "entities"
    provider: Provider
    model: str
    document: DocumentSummary
    entities: list[ProposedEntity]
    dropped: int
    sentences_dropped: int


class ProposedRelationship(_Message):
    """One relationship Proposal between two entities, named by their ids."""

    type: str
    from_id: str
    to_id: str
    date_from: str | None
    date_to: str | None
    date_precision: str
    sentence: str
    """The Proposal's first supporting sentence: the Document text's own slice
    ``text[sentence_start:sentence_end]``, in code points."""
    sentence_start: int
    sentence_end: int


class Relationships(_Message):
    """An ``extract`` run succeeded: the relationship Proposals ``model`` made.

    ``dropped`` counts the items of the Model's reply that were left out: those
    that were malformed, those with no supporting sentence found in the Document
    text, those that named an id Pass 1 never gave, and participant links that
    did not go from an entity to an Event. ``sentences_dropped`` counts the
    supporting sentences that were not found in the Document text.
    """

    kind: Literal["relationships"] = "relationships"
    provider: Provider
    model: str
    relationships: list[ProposedRelationship]
    dropped: int
    sentences_dropped: int


class Failed(_Message):
    """The run failed. ``message`` is one plain sentence with a next step.

    ``retry_at`` is when the quota comes back, as a UTC time, for a
    ``daily_quota`` failure whose Provider said when; null for every other.
    """

    kind: Literal["failed"] = "failed"
    provider: Provider
    reason: Reason
    message: str
    retry_at: AwareDatetime | None = None


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
