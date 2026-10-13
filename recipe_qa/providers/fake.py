"""Deterministic provider for tests and for running without a key.

Behaviour is injected as plain functions, so a test states exactly what
the "model" says (or raises) and then inspects what it was asked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence

from recipe_qa.corpus import Recipe
from recipe_qa.providers.base import Completion, Usage
from recipe_qa.schemas import Extraction, Generation

ExtractFn = Callable[[str], Extraction]
GenerateFn = Callable[[str, Extraction, list[Recipe]], Generation]


def in_domain(question: str) -> Extraction:
    return Extraction(in_domain=True, safety_question=False)


def cite_first(question: str, extraction: Extraction, recipes: list[Recipe]) -> Generation:
    top = recipes[0]
    return Generation(
        answerable=True,
        answer=f"(fake provider) See {top.title}.",
        source_ids=[top.id],
    )


class FakeProvider:
    def __init__(
        self,
        extract: ExtractFn = in_domain,
        generate: GenerateFn = cite_first,
        delay_s: float = 0.0,
        model: str = "fake",
    ) -> None:
        self.model = model
        self._extract = extract
        self._generate = generate
        self._delay_s = delay_s
        self.extract_calls = 0
        self.generate_calls: list[list[str]] = []
        """Recipe IDs passed to each generate call, in call order."""

    async def extract(self, question: str) -> Completion[Extraction]:
        self.extract_calls += 1
        await asyncio.sleep(self._delay_s)
        return Completion(self._extract(question), Usage(0, 0))

    async def generate(
        self, question: str, extraction: Extraction, recipes: Sequence[Recipe]
    ) -> Completion[Generation]:
        self.generate_calls.append([r.id for r in recipes])
        await asyncio.sleep(self._delay_s)
        return Completion(self._generate(question, extraction, list(recipes)), Usage(0, 0))
