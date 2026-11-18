"""Falling back to another Model when one fails (ISC-52).

``extract --fallback`` gives each pass a ``ModelChain``: the chosen Model first,
then the Models of ``FALLBACK_MODELS`` whose Provider has a key. A pass tries
the first Model that has not failed. When that Model still fails after its
retries, for any reason a fetch is not part of, the pass moves to the next one,
and the Model stays out of the rest of the run.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from respec_worker.client import ChatFailure
from respec_worker.extraction import BadOutput
from respec_worker.messages import Reason, failure_summary
from respec_worker.providers import FALLBACK_MODELS, PROVIDERS, Provider


@dataclass(frozen=True)
class Candidate:
    """One Model to call: where, which, and the key for that Provider."""

    provider: Provider
    model: str
    key: str = field(repr=False)


@dataclass(frozen=True)
class _Failed:
    candidate: Candidate
    reason: Reason
    message: str
    retry_at: datetime | None


class AllModelsFailed(Exception):
    """Every Model of the chain failed.

    ``provider`` is the last Model's Provider, and ``reason`` its reason, except
    that it is ``daily_quota`` when every Model ended on a daily quota, with
    ``retry_at`` the earliest time one comes back. ``message`` is the last
    Model's own message when only one was tried, and otherwise names each Model
    tried and why.
    """

    def __init__(
        self,
        provider: Provider,
        reason: Reason,
        message: str,
        retry_at: datetime | None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.reason = reason
        self.message = message
        self.retry_at = retry_at


def chain_for(chosen: Candidate, fallback: bool) -> list[Candidate]:
    """``chosen``, then, with ``fallback``, the Models of ``FALLBACK_MODELS``
    whose Provider has a key in the environment.

    ``chosen`` may be in the list too: once it has failed, its twin counts as
    failed with it, because ``Candidate`` compares by value.
    """
    chain = [chosen]
    if fallback:
        for provider, model in FALLBACK_MODELS:
            key = os.environ.get(PROVIDERS[provider].key_env, "").strip()
            if key:
                chain.append(Candidate(provider, model, key))
    return chain


class ModelChain:
    """The Models of one run, in order, and which of them have failed."""

    def __init__(self, models: list[Candidate]) -> None:
        self._models = models
        self._failed: list[_Failed] = []

    def _remaining(self) -> list[Candidate]:
        failed = {f.candidate for f in self._failed}
        return [model for model in self._models if model not in failed]

    def run[T](
        self,
        attempt: Callable[[Candidate], T],
        on_fallback: Callable[[Candidate, Reason, Candidate], None],
    ) -> tuple[T, Candidate]:
        """Call ``attempt`` with the first Model that has not failed, and return
        its result with that Model.

        ``attempt`` makes the Model call and reads its reply. If it raises a
        ``ChatFailure`` or a ``BadOutput``, the Model is marked failed,
        ``on_fallback(failed, reason, next)`` is called, and ``attempt`` is
        called with the next Model. Raises ``AllModelsFailed`` when none is left.
        """
        while True:
            candidate = self._remaining()[0]
            try:
                return attempt(candidate), candidate
            except ChatFailure as failure:
                failed = _Failed(
                    candidate, failure.reason, failure.message, failure.retry_at
                )
            except BadOutput as failure:
                failed = _Failed(candidate, "bad_output", failure.message, None)
            self._failed.append(failed)
            remaining = self._remaining()
            if not remaining:
                raise self._all_failed()
            on_fallback(candidate, failed.reason, remaining[0])

    def _all_failed(self) -> AllModelsFailed:
        last = self._failed[-1]
        provider = last.candidate.provider
        if len(self._failed) == 1:
            return AllModelsFailed(provider, last.reason, last.message, last.retry_at)
        daily = all(f.reason == "daily_quota" for f in self._failed)
        times = [f.retry_at for f in self._failed if f.retry_at is not None]
        retry_at = min(times) if daily and times else None
        message = failure_summary(
            [(f.candidate.provider, f.candidate.model, f.reason) for f in self._failed],
            retry_at=retry_at,
        )
        return AllModelsFailed(
            provider, "daily_quota" if daily else last.reason, message, retry_at
        )
