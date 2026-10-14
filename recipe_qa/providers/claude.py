"""Claude via the official SDK, with structured outputs (ADR 002).

Timeouts and the single retry are the SDK's (SPEC §2.3: 8 s per attempt,
one retry on timeout / connection error / 429 / 5xx). The client is built
on first use, so the app starts, serves /healthz and runs its tests
without a key.
"""

from __future__ import annotations

from collections.abc import Sequence

import anthropic
from pydantic import BaseModel, ValidationError

from recipe_qa.corpus import Recipe
from recipe_qa.generation import (
    EXTRACT_SYSTEM,
    GENERATE_SYSTEM,
    extract_message,
    generate_message,
)
from recipe_qa.providers.base import (
    Completion,
    ProviderInvalidOutput,
    ProviderUnavailable,
    Usage,
)
from recipe_qa.schemas import Extraction, Generation

# Effort "low": both tasks are reading tasks; the hard parts are in code.
EFFORT = "low"
EXTRACT_MAX_TOKENS = 1024
GENERATE_MAX_TOKENS = 2048


class ClaudeProvider:
    def __init__(self, model: str, timeout_s: float) -> None:
        self.model = model
        self._timeout_s = timeout_s
        self._client: anthropic.AsyncAnthropic | None = None

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(timeout=self._timeout_s, max_retries=1)
        return self._client

    async def _parse[T: BaseModel](
        self, system: str, user: str, schema: type[T], max_tokens: int
    ) -> Completion[T]:
        try:
            response = await self._get_client().messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": EFFORT},
            )
        except (anthropic.APITimeoutError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailable(type(exc).__name__) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code == 429 or exc.status_code >= 500:
                raise ProviderUnavailable(f"HTTP {exc.status_code}") from exc
            raise  # 4xx is our bug or our configuration: internal_error
        except ValidationError as exc:
            raise ProviderInvalidOutput(str(exc)) from exc

        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise ProviderInvalidOutput(f"stop_reason={response.stop_reason}")
        usage = Usage(response.usage.input_tokens, response.usage.output_tokens)
        return Completion(response.parsed_output, usage)

    async def extract(self, question: str) -> Completion[Extraction]:
        return await self._parse(
            EXTRACT_SYSTEM, extract_message(question), Extraction, EXTRACT_MAX_TOKENS
        )

    async def generate(
        self, question: str, extraction: Extraction, recipes: Sequence[Recipe]
    ) -> Completion[Generation]:
        return await self._parse(
            GENERATE_SYSTEM,
            generate_message(question, extraction, recipes),
            Generation,
            GENERATE_MAX_TOKENS,
        )
