"""ISC-37: a key is only ever sent to its own Provider's API host."""

from typing import get_args
from urllib.parse import urlsplit

import httpx
import pytest
from model_specs import spec_response

from respec_worker.budget import ModelSpec, lookup_model_spec
from respec_worker.client import ChatFailure, chat
from respec_worker.providers import PROVIDERS, Provider

EXPECTED_HOSTS: dict[Provider, str] = {
    "openrouter": "openrouter.ai",
    "gemini": "generativelanguage.googleapis.com",
}
AUTH_HEADERS = {"authorization", "x-goog-api-key"}
"""The headers a key may travel in."""
MESSAGES = [{"role": "user", "content": "ping"}]


def _recording(seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "pong"}}]})

    return httpx.MockTransport(handler)


def test_provider_hosts_table_covers_every_provider_with_its_own_host() -> None:
    """Every Provider has an entry, and no two entries share a host."""
    assert set(PROVIDERS) == set(get_args(Provider))
    assert {p: spec.host for p, spec in PROVIDERS.items()} == EXPECTED_HOSTS
    hosts = [spec.host for spec in PROVIDERS.values()]
    assert len(set(hosts)) == len(hosts)
    key_envs = [spec.key_env for spec in PROVIDERS.values()]
    assert len(set(key_envs)) == len(key_envs)


@pytest.mark.parametrize("provider", get_args(Provider))
def test_provider_hosts_request_goes_to_that_host_with_only_its_own_key(
    provider: Provider,
) -> None:
    """The request goes to exactly the Provider's host; the key is only in the
    Authorization header, and no other Provider's key appears anywhere."""
    keys = {p: f"secret-for-{p}-0123456789" for p in PROVIDERS}
    seen: list[httpx.Request] = []

    chat(provider, keys[provider], "some-model", MESSAGES, transport=_recording(seen))

    (request,) = seen
    assert request.url.scheme == "https"
    assert request.url.host == EXPECTED_HOSTS[provider]
    assert request.headers["authorization"] == f"Bearer {keys[provider]}"
    elsewhere = [str(request.url), request.content.decode()]
    elsewhere += [v for k, v in request.headers.items() if k != "authorization"]
    for key in keys.values():
        assert all(key not in part for part in elsewhere)
    for other, key in keys.items():
        if other != provider:
            assert key not in request.headers["authorization"]


def test_provider_hosts_redirect_is_not_followed() -> None:
    """A Provider answering with a redirect cannot lead the key to another host."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    with pytest.raises(ChatFailure) as excinfo:
        chat(
            "openrouter",
            "secret-0123456789",
            "m",
            MESSAGES,
            transport=httpx.MockTransport(handler),
        )

    assert [r.url.host for r in seen] == ["openrouter.ai"]
    assert excinfo.value.reason == "other"


@pytest.mark.parametrize("provider", get_args(Provider))
def test_provider_hosts_spec_lookup_stays_on_the_chat_host(provider: Provider) -> None:
    """The Model-spec endpoint is in the table and shares the chat endpoint's
    host, so the key reaches nothing the chat call does not already trust."""
    models_url = urlsplit(PROVIDERS[provider].models_url)

    assert models_url.hostname == EXPECTED_HOSTS[provider]
    assert models_url.scheme == "https"


@pytest.mark.parametrize("provider", get_args(Provider))
def test_provider_hosts_spec_lookup_goes_to_that_host_with_only_its_own_key(
    provider: Provider,
) -> None:
    """The lookup request goes to exactly the Provider's host; the key is in a
    header only, never in the URL or the body, and no other key appears."""
    keys = {p: f"secret-for-{p}-0123456789" for p in PROVIDERS}
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return spec_response(provider, "some-model", 100_000, 8_000)

    found = lookup_model_spec(
        provider, keys[provider], "some-model", transport=httpx.MockTransport(handler)
    )

    assert found == ModelSpec(context_window=100_000, max_output=8_000)
    (request,) = seen
    assert request.method == "GET"
    assert request.url.scheme == "https"
    assert request.url.host == EXPECTED_HOSTS[provider]
    assert request.content == b""
    carried = [v for k, v in request.headers.items() if k.lower() in AUTH_HEADERS]
    assert any(keys[provider] in v for v in carried)
    elsewhere = [str(request.url)]
    elsewhere += [
        v for k, v in request.headers.items() if k.lower() not in AUTH_HEADERS
    ]
    for key in keys.values():
        assert all(key not in part for part in elsewhere)
    for other, key in keys.items():
        if other != provider:
            assert all(key not in v for v in carried)


def test_provider_hosts_spec_lookup_redirect_is_not_followed() -> None:
    """A Provider answering the lookup with a redirect cannot lead the key to
    another host."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/steal"})

    found = lookup_model_spec(
        "gemini", "secret-0123456789", "m", transport=httpx.MockTransport(handler)
    )

    assert found is None
    assert [r.url.host for r in seen] == [EXPECTED_HOSTS["gemini"]]
