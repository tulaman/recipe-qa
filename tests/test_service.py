"""Pipeline details visible only in the request trace (SPEC §8, §9.7)."""

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from recipe_qa.corpus import Recipe, load_manifest, load_recipes
from recipe_qa.providers.base import Completion, Usage
from recipe_qa.providers.fake import FakeProvider, GenerateFn, cite_first
from recipe_qa.retrieval import BM25Index
from recipe_qa.schemas import Extraction, Generation
from recipe_qa.service import AskService, UpstreamInvalid

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


def service_extracting(
    safety: bool = False, generate: GenerateFn = cite_first, **constraints: Any
) -> AskService:
    provider = FakeProvider(
        extract=lambda q: Extraction(in_domain=True, safety_question=safety, **constraints),
        generate=generate,
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


# --- refusal policy: guarantee, fact, filter (SPEC §5) -----------------------


def test_a_guarantee_question_is_refused_with_safety_and_cites_the_matched_recipes() -> None:
    service = service_extracting(safety=True)
    trace: dict[str, Any] = {}
    response = asyncio.run(service.ask("Is the baklava nut-free?", "rid", trace))

    assert (response.refused, response.refusal_reason, response.answer) == (True, "safety", None)
    # Retrieval runs without generation, so the user can read the list themselves.
    assert {c.title for c in response.citations} == {"Baklava I", "Baklava II"}
    assert service.provider.generate_calls == []
    assert "retrieve" in trace["stages_ms"]
    message = response.refusal_message or ""
    assert "guarantee" in message and "ingredient list" in message
    assert message.count(". ") <= 1  # at most two sentences (SPEC §2.1)


def test_a_factual_allergen_question_is_answered_from_the_recipe() -> None:
    service = service_extracting()
    response = asyncio.run(service.ask("Does the baklava contain nuts?", "rid", {}))

    assert (response.refused, response.refusal_reason) == (False, None)
    (call,) = service.provider.generate_calls
    assert {"baklava-i", "baklava-ii"} <= set(call)
    assert response.citations[0].title in {"Baklava I", "Baklava II"}


DESSERTS_WITHOUT_NUTS = "Which desserts can I make without nuts?"
NOT_MENTIONED = "Apple Crisp works; the ingredient list on the source page does not mention nuts."
GUARANTEES = [
    "Apple Crisp is safe for a nut allergy.",
    "Apple Crisp is SAFE to serve.",
    "You can safely serve Apple Crisp.",
    "Apple Crisp is not unsafe.",
    "For safety, choose Apple Crisp.",
    "Apple Crisp is nut-free.",
    "Apple Crisp is nut free.",
    "It is gluten  free too.",
    "Apple Crisp is free of nuts.",
    "Apple Crisp is free from nuts.",
]


def answering(*answers: str) -> GenerateFn:
    """Answer with each text in turn, citing the top candidate."""
    texts = iter(answers)
    return lambda q, ex, rs: Generation(answerable=True, answer=next(texts), source_ids=[rs[0].id])


@pytest.mark.parametrize("guarantee", GUARANTEES)
def test_a_must_exclude_answer_with_guarantee_wording_is_retried(guarantee: str) -> None:
    service = service_extracting(generate=answering(guarantee, NOT_MENTIONED), must_exclude=["nut"])
    trace: dict[str, Any] = {}
    response = asyncio.run(service.ask(DESSERTS_WITHOUT_NUTS, "rid", trace))

    assert response.refused is False
    assert "safe" not in (response.answer or "").lower()
    assert response.answer == NOT_MENTIONED
    assert len(service.provider.generate_calls) == 2
    assert len(trace["invalid_output"]) == 1


def test_a_must_exclude_answer_that_keeps_guarantee_wording_is_upstream_invalid() -> None:
    service = service_extracting(generate=answering(*GUARANTEES[:2]), must_exclude=["nut"])
    with pytest.raises(UpstreamInvalid):
        asyncio.run(service.ask(DESSERTS_WITHOUT_NUTS, "rid", {}))


@pytest.mark.parametrize(
    "answer",
    [
        "Beat 4 free-range eggs; the source page does not mention nuts.",
        "Use free range eggs; the source page does not mention nuts.",
        "Feel free to add cinnamon; the source page does not mention nuts.",
    ],
)
def test_free_that_is_not_an_allergen_claim_passes_the_guard(answer: str) -> None:
    service = service_extracting(generate=answering(answer), must_exclude=["nut"])
    response = asyncio.run(service.ask(DESSERTS_WITHOUT_NUTS, "rid", {}))
    assert response.answer == answer


def test_recipe_wording_like_heat_safe_is_kept_when_nothing_is_excluded() -> None:
    # The guard is scoped to must_exclude: recipe text itself says "heat-safe".
    answer = "Steam it in heat-safe tins."
    service = service_extracting(generate=answering(answer))
    response = asyncio.run(service.ask("How do I make nian gao?", "rid", {}))
    assert response.answer == answer
