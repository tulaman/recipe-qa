"""How provider HTTP errors map to ProviderUnavailable (SPEC §2.3, v0.7).

A provider that refuses to serve the account (rate limit, spend limit,
billing) is an outage for the user: 503. Any other 4xx is our bug: it is
re-raised and becomes 500. No network: the SDK client is replaced.
"""

import asyncio
from typing import Any

import anthropic
import httpx
import pytest

from recipe_qa.providers.base import ProviderUnavailable
from recipe_qa.providers.claude import ClaudeProvider

SPEND_LIMIT = (
    "You have reached your specified API usage limits. "
    "You will regain access on 2026-11-01 at 00:00 UTC."
)
WORKSPACE_SPEND_LIMIT = (
    "You have reached your specified workspace API usage limits. "
    "You will regain access on 2026-11-01 at 00:00 UTC."
)


def status_error(status: int, error_type: str, message: str) -> anthropic.APIStatusError:
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    body = {"type": "error", "error": {"type": error_type, "message": message}}
    return anthropic.APIStatusError(message, response=response, body=body)


def provider_raising(exc: Exception) -> ClaudeProvider:
    class Messages:
        async def parse(self, **_: Any) -> None:
            raise exc

    class Client:
        messages = Messages()

    provider = ClaudeProvider(model="test", timeout_s=1)
    provider._client = Client()  # type: ignore[assignment]
    return provider


def extract(provider: ClaudeProvider) -> None:
    asyncio.run(provider.extract("How do I make baklava?"))


@pytest.mark.parametrize(
    "exc",
    [
        status_error(400, "invalid_request_error", SPEND_LIMIT),
        status_error(400, "invalid_request_error", WORKSPACE_SPEND_LIMIT),
        status_error(402, "billing_error", "There is an issue with your billing."),
        status_error(429, "rate_limit_error", "Number of requests has exceeded your rate limit."),
        status_error(529, "overloaded_error", "Overloaded"),
    ],
    ids=["spend-limit", "workspace-spend-limit", "billing", "rate-limit", "overloaded"],
)
def test_the_provider_refusing_the_account_is_unavailable(exc: anthropic.APIStatusError) -> None:
    with pytest.raises(ProviderUnavailable):
        extract(provider_raising(exc))


@pytest.mark.parametrize(
    "exc",
    [
        status_error(400, "invalid_request_error", "max_tokens: Field required"),
        status_error(401, "authentication_error", "invalid x-api-key"),
        status_error(404, "not_found_error", "model: test"),
    ],
    ids=["bad-request", "auth", "not-found"],
)
def test_any_other_client_error_is_our_bug(exc: anthropic.APIStatusError) -> None:
    with pytest.raises(anthropic.APIStatusError):
        extract(provider_raising(exc))


def test_a_body_without_the_expected_shape_is_still_our_bug() -> None:
    response = httpx.Response(
        400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    exc = anthropic.APIStatusError("bad", response=response, body="not json")
    with pytest.raises(anthropic.APIStatusError):
        extract(provider_raising(exc))
