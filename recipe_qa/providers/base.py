"""What the service needs from a model provider, and how a provider fails.

Two failure kinds map to two HTTP statuses (SPEC §2.3): an unreachable
provider is 503, a provider that answers with output we cannot use is 502.
Neither is ever turned into a business refusal.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from recipe_qa.corpus import Recipe
from recipe_qa.schemas import Extraction, Generation


class ProviderError(Exception):
    pass


class ProviderUnavailable(ProviderError):
    """Timeout, connection error, rate limit or 5xx after the client's retry."""


class ProviderInvalidOutput(ProviderError):
    """The provider answered, but not with output matching the schema."""


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Completion[T]:
    value: T
    usage: Usage


class Provider(Protocol):
    model: str

    async def extract(self, question: str) -> Completion[Extraction]: ...

    async def generate(
        self, question: str, extraction: Extraction, recipes: Sequence[Recipe]
    ) -> Completion[Generation]: ...
