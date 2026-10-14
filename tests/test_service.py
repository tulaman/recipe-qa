"""Pipeline details visible only in the request trace (SPEC §8, §9.7)."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from recipe_qa.corpus import Recipe, load_manifest, load_recipes
from recipe_qa.providers.base import Completion, Usage
from recipe_qa.providers.fake import FakeProvider
from recipe_qa.retrieval import BM25Index
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


# --- step 5: eligibility over the whole corpus, before ranking (SPEC §3, §4) -----

RECIPES = load_recipes(DATA / "recipes.jsonl")
VEGAN_RICE_UNDER_30 = {"diet": "vegan", "max_total_minutes": 30, "must_include": ["rice"]}
VEGAN_POTATO_UNDER_30 = {"diet": "vegan", "max_total_minutes": 30, "must_include": ["potato"]}


def service_extracting(safety: bool = False, **constraints: Any) -> AskService:
    provider = FakeProvider(
        extract=lambda q: Extraction(in_domain=True, safety_question=safety, **constraints)
    )
    return AskService(RECIPES, load_manifest(DATA / "manifest.json"), provider, deadline_s=5)


def test_the_only_eligible_recipe_is_found_even_when_bm25_ranks_it_sixth_or_lower() -> None:
    question = "What rice dish can I make in under 30 minutes?"
    unfiltered = [h.recipe.id for h in BM25Index(RECIPES).search(question, k=len(RECIPES))]
    assert unfiltered.index("southwestern-black-beans-and-rice") >= 5  # the premise

    service = service_extracting(**VEGAN_RICE_UNDER_30)
    trace: dict[str, Any] = {}
    response = asyncio.run(service.ask(question, "rid", trace))

    assert service.provider.generate_calls == [["southwestern-black-beans-and-rice"]]
    assert [c.title for c in response.citations] == ["Southwestern Black Beans and Rice"]
    assert trace["eligible_count"] == 1
    assert len(trace["rejected"]) == len(RECIPES) - 1


def test_no_eligible_recipe_is_out_of_corpus_naming_the_constraints() -> None:
    service = service_extracting(**VEGAN_POTATO_UNDER_30)
    trace: dict[str, Any] = {}
    question = "Is there a vegan dish with potatoes that I can make in under 30 minutes?"
    response = asyncio.run(service.ask(question, "rid", trace))

    assert (response.refused, response.refusal_reason, response.citations) == (
        True,
        "out_of_corpus",
        [],
    )
    assert service.provider.generate_calls == []
    message = response.refusal_message or ""
    for words in ("vegan", "under 30 minutes", "with potato"):
        assert words in message
    assert message.count(". ") <= 1  # at most two sentences (SPEC §2.1)
    assert trace["eligible_count"] == 0
    assert set(trace["rejected"]) == {r.id for r in RECIPES}
    # Indian Potatoes is vegan and has potatoes; only its unknown time rules it out.
    assert trace["rejected"]["indian-potatoes"] == ["total time unknown, need < 30 min"]


def test_without_constraints_every_recipe_is_eligible() -> None:
    trace: dict[str, Any] = {}
    asyncio.run(service_extracting().ask("How do I make baklava?", "rid", trace))
    assert trace["eligible_count"] == len(RECIPES)
    assert trace["rejected"] == {}


def test_the_safety_gate_runs_before_the_constraint_filter() -> None:
    service = service_extracting(safety=True, **VEGAN_POTATO_UNDER_30)
    response = asyncio.run(service.ask("Is the kheer nut-free?", "rid", {}))
    assert response.refusal_reason == "safety"
    assert service.provider.generate_calls == []
