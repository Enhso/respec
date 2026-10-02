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

Reason = Literal["auth", "quota", "rate_limit", "model_unavailable", "network", "other"]
"""Why a call failed, in terms the operator can act on."""


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


class Failed(_Message):
    """The call failed. ``message`` is one plain sentence with a next step."""

    kind: Literal["failed"] = "failed"
    provider: Provider
    reason: Reason
    message: str


Message = Annotated[Started | Done | Failed, Field(discriminator="kind")]

MESSAGE_ADAPTER: TypeAdapter[Message] = TypeAdapter(Message)
"""Validates one parsed or raw JSON message into the right model."""


def emit(message: Started | Done | Failed) -> None:
    """Write ``message`` to stdout as one JSON line and flush."""
    sys.stdout.write(orjson.dumps(message.model_dump(mode="json")).decode() + "\n")
    sys.stdout.flush()
