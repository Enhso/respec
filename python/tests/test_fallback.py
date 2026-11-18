"""ISC-52: with `extract --fallback`, a pass whose Model still fails after its
retries moves to the next Model of an ordered list across the Providers with
keys, and the run says so."""

from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

import httpx
import orjson
import pytest
from model_specs import spec_response
from test_call_deadline import FakeClock, Trickle
from test_cli import ARTICLES, SHIPMENT_ENTITIES, SHIPMENT_LINKS

from respec_worker import client
from respec_worker.cli import main
from respec_worker.messages import (
    MESSAGE_ADAPTER,
    AnyMessage,
    Entities,
    Failed,
    Progress,
    Relationships,
)
from respec_worker.providers import PROVIDERS, Provider

KEYS: dict[Provider, str] = {
    "gemini": "gemini-key-0123456789",
    "openrouter": "openrouter-key-0123456789",
}
ARTICLE_URL = "https://news.example/articles/42"
PRIMARY: tuple[Provider, str] = ("gemini", "primary-model")
ModelId = tuple[Provider, str]
# A fixed list, so these tests do not change each time the real one does.
TEST_FALLBACK: tuple[ModelId, ...] = (
    ("gemini", "gemini-fallback-a"),
    ("openrouter", "openrouter-fallback-a"),
    ("gemini", "gemini-fallback-b"),
    ("openrouter", "openrouter-fallback-b"),
)
FIRST, SECOND, THIRD, FOURTH = TEST_FALLBACK


@pytest.fixture(autouse=True)
def _fixed_fallback_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("respec_worker.fallback.FALLBACK_MODELS", TEST_FALLBACK)


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


PASS_1 = _chat(orjson.dumps({"entities": SHIPMENT_ENTITIES}).decode())
PASS_2 = _chat(orjson.dumps({"relationships": SHIPMENT_LINKS}).decode())
BOTH_PASSES = [PASS_1, PASS_2]
OVERLOADED = httpx.Response(503, text="high demand")
RATE_LIMITED = httpx.Response(429, text="slow down")
JUNK = _chat("I found nothing worth reporting.")


def _output_limit(model: str) -> int:
    """The max output the fake Provider states for ``model``: one per Model."""
    return len(model) * 1000


class FakeNetwork:
    """The article site, each Provider's Model-spec lookup, and chat replies.

    ``replies`` gives each Model's answers in turn (the last repeats). A call to
    a Model with no entry, or one that carries the wrong Provider's key, fails
    the test.
    """

    def __init__(self, replies: dict[ModelId, list[httpx.Response]]) -> None:
        self.replies = {model: list(queue) for model, queue in replies.items()}
        self.page = (ARTICLES / "shipment_trace.html").read_text(encoding="utf-8")
        self.requests: list[httpx.Request] = []
        self.calls: list[ModelId] = []
        self.budgets: list[int] = []

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _provider_of(self, request: httpx.Request) -> Provider:
        (provider,) = [
            p for p, spec in PROVIDERS.items() if spec.host == request.url.host
        ]
        return provider

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "news.example":
            return httpx.Response(200, text=self.page)
        provider = self._provider_of(request)
        if request.method == "GET":
            assert request.headers.get("authorization", "").endswith(
                KEYS[provider]
            ) or (request.headers.get("x-goog-api-key") == KEYS[provider])
            if provider == "gemini":
                model = unquote(request.url.path.rsplit("/", 1)[-1])
                return spec_response(provider, model, 2_000_000, _output_limit(model))
            models = [m for p, m in self.replies if p == "openrouter"]
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": m,
                            "context_length": 2_000_000,
                            "top_provider": {"max_completion_tokens": _output_limit(m)},
                        }
                        for m in models
                    ]
                },
            )
        assert request.headers["authorization"] == f"Bearer {KEYS[provider]}"
        body = orjson.loads(request.content)
        model_id = (provider, body["model"])
        self.calls.append(model_id)
        self.budgets.append(body["max_tokens"])
        queue = self.replies[model_id]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def _run(
    network: FakeNetwork,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *flags: str,
    primary: ModelId = PRIMARY,
    keys: tuple[Provider, ...] = ("gemini", "openrouter"),
) -> tuple[int, list[AnyMessage]]:
    """Run `extract --url` for ``primary`` over the fake network with only the
    Providers in ``keys`` having a key; return the exit code and the messages."""
    for provider, spec in PROVIDERS.items():
        if provider in keys:
            monkeypatch.setenv(spec.key_env, KEYS[provider])
        else:
            monkeypatch.delenv(spec.key_env, raising=False)
    provider, model = primary
    code = main(
        [
            "extract",
            "--provider",
            provider,
            "--model",
            model,
            "--url",
            ARTICLE_URL,
            *flags,
        ],
        transport=network.transport,
        sleep=lambda seconds: None,
    )
    captured = capsys.readouterr()
    assert not any(key in captured.out + captured.err for key in KEYS.values())
    return code, [
        MESSAGE_ADAPTER.validate_json(line) for line in captured.out.splitlines()
    ]


def _falling_back(messages: list[AnyMessage]) -> list[Progress]:
    return [
        m for m in messages if isinstance(m, Progress) and m.stage == "falling_back"
    ]


def test_fallback_the_primary_fails_after_its_retries_and_the_next_model_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The primary is 503 on all six tries; the first Model of the list answers
    both passes. `falling_back` names the failed Model, why, and the next one,
    and `entities` and `relationships` name the Model that made them."""
    network = FakeNetwork({PRIMARY: [OVERLOADED], FIRST: BOTH_PASSES})

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    assert code == 0
    assert network.calls == [PRIMARY] * 6 + [FIRST] * 2
    (notice,) = _falling_back(messages)
    assert notice == Progress(
        provider=FIRST[0],
        stage="falling_back",
        detail=f"{PRIMARY[1]} failed (Google Gemini is overloaded right now); "
        f"trying {FIRST[1]} on Google Gemini",
        pass_number=1,
        pass_count=2,
    )
    entities = next(m for m in messages if isinstance(m, Entities))
    relationships = messages[-1]
    assert isinstance(relationships, Relationships)
    assert (entities.provider, entities.model) == FIRST
    assert (relationships.provider, relationships.model) == FIRST
    assert not [m for m in messages if isinstance(m, Failed)]
    assert network.budgets[:6] == [_output_limit(PRIMARY[1])] * 6
    assert network.budgets[6:] == [_output_limit(FIRST[1])] * 2


def test_fallback_pass_2_moves_to_the_next_model_without_redoing_pass_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pass 1 is the primary's and Pass 2 the next Model's, and `relationships`
    says which Model made which."""
    network = FakeNetwork({PRIMARY: [PASS_1, RATE_LIMITED], FIRST: [PASS_2]})

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    assert code == 0
    assert network.calls == [PRIMARY] + [PRIMARY] * 6 + [FIRST]
    (notice,) = _falling_back(messages)
    assert notice.pass_number == 2 and notice.provider == FIRST[0]
    assert "rate limit was reached" in notice.detail
    entities = next(m for m in messages if isinstance(m, Entities))
    relationships = messages[-1]
    assert isinstance(relationships, Relationships)
    assert entities.model == PRIMARY[1] and relationships.model == FIRST[1]
    assert relationships.relationships


def test_fallback_a_model_that_failed_is_skipped_in_the_next_pass(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A fails Pass 1, B answers it, then B fails Pass 2: Pass 2 goes on to C,
    not back to A."""
    network = FakeNetwork(
        {
            PRIMARY: [httpx.Response(402, text="pay")],
            FIRST: [PASS_1, JUNK],
            SECOND: [PASS_2],
        }
    )

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    assert code == 0
    assert network.calls == [PRIMARY, FIRST, FIRST, SECOND]
    first, second = _falling_back(messages)
    assert (first.pass_number, second.pass_number) == (1, 2)
    assert f"trying {SECOND[1]} on OpenRouter" in second.detail
    assert second.provider == SECOND[0]


def test_fallback_a_reply_that_is_not_json_falls_back_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bad output comes from parsing, after the call, and still moves on."""
    network = FakeNetwork({PRIMARY: [JUNK], FIRST: BOTH_PASSES})

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    assert code == 0
    assert network.calls == [PRIMARY, FIRST, FIRST]
    (notice,) = _falling_back(messages)
    assert "not in the form Respec needs" in notice.detail


def test_fallback_is_off_unless_asked_for(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without `--fallback` the run stays on its Model and fails with that
    Model's own failure, as the evals need so that a score never mixes Models."""
    network = FakeNetwork({PRIMARY: [OVERLOADED], FIRST: BOTH_PASSES})

    code, messages = _run(network, capsys, monkeypatch)

    final = messages[-1]
    assert code == 1
    assert isinstance(final, Failed) and final.reason == "overloaded"
    assert final.message == (
        "Google Gemini is overloaded right now; try again in a few minutes, or "
        "later if it stays busy."
    )
    assert set(network.calls) == {PRIMARY}
    assert not _falling_back(messages)


def test_fallback_skips_providers_without_a_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no OpenRouter key the walk goes from Gemini's first Model to its
    other one, and never calls OpenRouter."""
    network = FakeNetwork(
        {PRIMARY: [OVERLOADED], FIRST: [OVERLOADED], THIRD: BOTH_PASSES}
    )

    code, messages = _run(network, capsys, monkeypatch, "--fallback", keys=("gemini",))

    assert code == 0
    assert network.calls == [PRIMARY] * 6 + [FIRST] * 6 + [THIRD] * 2
    assert all(
        request.url.host != PROVIDERS["openrouter"].host for request in network.requests
    )
    assert [n.provider for n in _falling_back(messages)] == [FIRST[0], THIRD[0]]


def test_fallback_the_chosen_model_is_not_tried_twice(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the chosen Model is also in the list, it is tried once, first."""
    network = FakeNetwork({FIRST: [OVERLOADED], SECOND: BOTH_PASSES})

    code, _ = _run(network, capsys, monkeypatch, "--fallback", primary=FIRST)

    assert code == 0
    assert network.calls == [FIRST] * 6 + [SECOND] * 2


def test_fallback_a_failed_fetch_ends_the_run_without_calling_a_model(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    network = FakeNetwork({PRIMARY: BOTH_PASSES})
    network.page = "<html><body></body></html>"

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    final = messages[-1]
    assert code == 1
    assert isinstance(final, Failed) and final.reason == "fetch"
    assert network.calls == []


def test_fallback_when_every_model_fails_the_failure_names_each_and_why(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each Model failed in its own way. The reason is the last one's, the
    Provider is the last one's, and there is no `retry_at`."""
    network = FakeNetwork(
        {
            PRIMARY: [OVERLOADED],
            FIRST: [RATE_LIMITED],
            SECOND: [httpx.Response(402, text="pay")],
            THIRD: [httpx.Response(404, text="gone")],
            FOURTH: [httpx.Response(401, text="no")],
        }
    )

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    final = messages[-1]
    assert code == 1
    assert isinstance(final, Failed)
    assert (final.provider, final.reason, final.retry_at) == (FOURTH[0], "auth", None)
    assert final.message == (
        "No Model could finish this article. "
        f"{PRIMARY[1]}: Google Gemini is overloaded right now. "
        f"{FIRST[1]}: The Google Gemini rate limit was reached. "
        f"{SECOND[1]}: OpenRouter says the account is out of credit. "
        f"{THIRD[1]}: Google Gemini is not offering the model {THIRD[1]} "
        "to this account. "
        f"{FOURTH[1]}: OpenRouter rejected the key. "
        "Check the key in Respec's settings and save it again."
    )
    assert len(_falling_back(messages)) == 4


def _gemini_day(seconds: int) -> httpx.Response:
    detail = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel"}],
        },
        {
            "@type": "type.googleapis.com/google.rpc.RetryInfo",
            "retryDelay": f"{seconds}s",
        },
    ]
    return httpx.Response(429, content=orjson.dumps([{"error": {"details": detail}}]))


def _openrouter_day(reset: datetime) -> httpx.Response:
    error = {
        "message": "Rate limit exceeded: free-models-per-day",
        "metadata": {
            "headers": {"X-RateLimit-Reset": str(int(reset.timestamp() * 1000))}
        },
    }
    return httpx.Response(429, content=orjson.dumps({"error": error}))


@pytest.mark.parametrize("openrouter_is_earlier", [True, False])
def test_fallback_every_model_on_a_daily_quota_ends_at_the_earliest_retry_time(
    openrouter_is_earlier: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    openrouter_reset = now + timedelta(hours=1 if openrouter_is_earlier else 9)
    gemini = _gemini_day(4 * 3600)
    openrouter = _openrouter_day(openrouter_reset)
    network = FakeNetwork(
        {
            PRIMARY: [gemini],
            FIRST: [gemini],
            SECOND: [openrouter],
            THIRD: [gemini],
            FOURTH: [openrouter],
        }
    )

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    final = messages[-1]
    assert code == 1
    assert isinstance(final, Failed) and final.reason == "daily_quota"
    assert final.retry_at is not None
    if openrouter_is_earlier:
        assert final.retry_at == openrouter_reset
    else:
        assert (
            now + timedelta(hours=4)
            <= final.retry_at
            <= now + timedelta(hours=4, seconds=5)
        )
    assert final.retry_at.strftime("%Y-%m-%d %H:%M UTC") in final.message
    assert network.calls == [PRIMARY, FIRST, SECOND, THIRD, FOURTH]


def test_fallback_a_daily_quota_among_other_failures_is_not_the_runs_reason_unless_last(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only when every Model ended on a daily quota does the run say so with a
    time. Otherwise the last Model's reason stands and `retry_at` is null."""
    soon = datetime.now(UTC) + timedelta(hours=1)
    network = FakeNetwork(
        {
            PRIMARY: [OVERLOADED],
            FIRST: [_gemini_day(3600)],
            SECOND: [_openrouter_day(soon)],
            THIRD: [_gemini_day(3600)],
            FOURTH: [_openrouter_day(soon)],
        }
    )

    _, messages = _run(network, capsys, monkeypatch, "--fallback")

    final = messages[-1]
    assert isinstance(final, Failed)
    assert (final.reason, final.retry_at) == ("daily_quota", None)
    assert "Try again once the quota resets" in final.message


def test_fallback_a_call_that_hangs_past_its_deadline_falls_back(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Provider that trickles bytes for longer than the deadline fails the call
    as `network`, which is not retried, and the pass moves on at once."""
    clock = FakeClock()
    real_chat = client.chat
    monkeypatch.setattr(
        client, "chat", lambda *args, **kwargs: real_chat(*args, clock=clock, **kwargs)
    )
    hung = Trickle(clock, seconds=60, chunks=1000)
    network = FakeNetwork(
        {PRIMARY: [httpx.Response(200, stream=hung)], FIRST: BOTH_PASSES}
    )

    code, messages = _run(network, capsys, monkeypatch, "--fallback")

    assert code == 0
    assert network.calls == [PRIMARY, FIRST, FIRST]
    assert hung.sent == 11
    (notice,) = _falling_back(messages)
    assert "did not answer in time" in notice.detail
