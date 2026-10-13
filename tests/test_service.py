"""Pipeline details visible only in the request trace (SPEC §8, §9.7)."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from recipe_qa.corpus import Recipe, load_manifest, load_recipes
from recipe_qa.providers.base import Completion, Usage
from recipe_qa.providers.fake import FakeProvider
from recipe_qa.schemas import Extraction, Generation
from recipe_qa.service import AskService

DATA = Path(__file__).resolve().parent.parent / "data"


class BilledFake(FakeProvider):
    """Reports 100 input / 10 output tokens for every generate call."""

    async def generate(
        self, question: str, extraction: Extraction, recipes: Sequence[Recipe]
    ) -> Completion[Generation]:
        completion = await super().generate(question, extraction, recipes)
        return Completion(completion.value, Usage(100, 10))


def test_rejected_generations_are_still_counted_in_usage() -> None:
    # The first answer cites a recipe it was not given and is retried; both
    # calls were billed, so both must appear in the trace.
    answers = iter(["rice-pudding", "baklava-i"])
    provider = BilledFake(
        generate=lambda q, ex, rs: Generation(
            answerable=True, answer="Layer the filo.", source_ids=[next(answers)]
        )
    )
    service = AskService(
        load_recipes(DATA / "recipes.jsonl"),
        load_manifest(DATA / "manifest.json"),
        provider,
        deadline_s=5,
    )
    trace: dict[str, Any] = {}
    response = asyncio.run(service.ask("How do I make baklava?", "rid", trace))

    assert response.refused is False
    generate_usage = [u for u in trace["usage"] if u["stage"] == "generate"]
    assert len(generate_usage) == 2
    assert len(trace["invalid_output"]) == 1
