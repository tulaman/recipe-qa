"""The /ask pipeline (SPEC §3): model reads, code decides.

Every decision that can be made by code is made here, deterministically:
the domain and safety gates, which recipes the model may see, whether
its citations are allowed, and how IDs become titles and URLs. The model
only extracts and phrases.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from recipe_qa.constraints import describe, why_ineligible
from recipe_qa.corpus import Manifest, Recipe
from recipe_qa.providers.base import (
    Completion,
    Provider,
    ProviderInvalidOutput,
    ProviderUnavailable,
)
from recipe_qa.retrieval import BM25Index, Hit
from recipe_qa.schemas import (
    AskResponse,
    Citation,
    Extraction,
    Generation,
    Meta,
    RefusalReason,
)

RETRIEVE_K = 5
CONTEXT_K = 3
# A safety refusal cites recipes scoring at least this share of the best
# match, so "Is the baklava nut-free?" links both baklavas and nothing else.
SAFETY_MATCH_RATIO = 0.5

REFUSAL_MESSAGES: dict[RefusalReason, str] = {
    "out_of_domain": (
        "I can only answer questions about the recipes in this collection, and this "
        "question is not about cooking. Try asking about a dish, an ingredient or a cooking time."
    ),
    "out_of_corpus": (
        "I can only answer from the recipes in this collection, and none of them covers this. "
        "Try asking about a specific dish or relaxing a constraint."
    ),
    "safety": (
        "I can't guarantee that a recipe is free of an allergen or safe for a particular "
        "condition. Please check the ingredient list on the linked recipe page yourself."
    ),
}


def unmet_constraints_message(constraints: Extraction) -> str:
    """An out_of_corpus message that names what no recipe satisfies (SPEC §5)."""
    return (
        "I can only answer from the recipes in this collection, and none of them matches "
        f"all of: {'; '.join(describe(constraints))}. "
        "Try relaxing a constraint or asking about a specific dish."
    )


class UpstreamUnavailable(Exception):
    """Maps to 503 upstream_unavailable."""


class UpstreamInvalid(Exception):
    """Maps to 502 upstream_invalid."""


@dataclass(frozen=True)
class Outcome:
    answer: str | None
    cited_ids: list[str]
    refusal: RefusalReason | None = None
    message: str | None = None
    """Refusal message when the reason's default is not specific enough."""


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]
    eligible_count: int
    rejected: dict[str, list[str]]
    """Recipe ID → every constraint it failed."""


def retrieve(
    index: BM25Index,
    recipes: list[Recipe],
    question: str,
    constraints: Extraction,
    k: int = RETRIEVE_K,
) -> Retrieval:
    """SPEC §3 steps 5-6: filter the whole corpus by code, then rank what is left.

    Filtering first means an eligible recipe is found however low BM25
    would rank it among all recipes.
    """
    rejected = {r.id: reasons for r in recipes if (reasons := why_ineligible(r, constraints))}
    eligible = [r.id for r in recipes if r.id not in rejected]
    hits = index.search(question, eligible, k=k) if eligible else []
    return Retrieval(hits, len(eligible), rejected)


class AskService:
    def __init__(
        self,
        recipes: list[Recipe],
        manifest: Manifest,
        provider: Provider,
        deadline_s: float,
    ) -> None:
        self.provider = provider
        self.corpus_version = manifest.corpus_version
        self._deadline_s = deadline_s
        self._recipes = list(recipes)
        self._index = BM25Index(recipes)
        # I3: the only source of citations is the manifest.
        self._citations = {e.id: Citation(title=e.title, url=e.url) for e in manifest.recipes}

    async def ask(self, question: str, request_id: str, trace: dict[str, Any]) -> AskResponse:
        started = time.perf_counter()
        trace.update(stages_ms={}, usage=[])
        try:
            async with asyncio.timeout(self._deadline_s):
                outcome = await self._pipeline(question, trace)
        except TimeoutError as exc:
            raise UpstreamUnavailable(f"request deadline {self._deadline_s}s exceeded") from exc

        trace.update(refusal_reason=outcome.refusal, cited_ids=outcome.cited_ids)
        return AskResponse(
            answer=outcome.answer,
            citations=[self._citations[rid] for rid in outcome.cited_ids],
            refused=outcome.refusal is not None,
            refusal_reason=outcome.refusal,
            refusal_message=(
                (outcome.message or REFUSAL_MESSAGES[outcome.refusal]) if outcome.refusal else None
            ),
            meta=Meta(
                request_id=request_id,
                model=self.provider.model,
                corpus_version=self.corpus_version,
                latency_ms=round((time.perf_counter() - started) * 1000),
            ),
        )

    async def _pipeline(self, question: str, trace: dict[str, Any]) -> Outcome:
        # 3. extraction
        extraction = await self._call_once_retry(
            "extract", lambda: self.provider.extract(question), trace
        )
        trace["extraction"] = extraction.model_dump()

        # 4. deterministic gates
        if not extraction.in_domain:
            return Outcome(None, [], "out_of_domain")
        if extraction.safety_question:
            hits = self._index.search(question, k=CONTEXT_K)
            best = hits[0].score if hits else 0.0
            matched = [h.recipe.id for h in hits if h.score >= SAFETY_MATCH_RATIO * best]
            return Outcome(None, matched, "safety")

        # 5-6. eligibility over the whole corpus, then ranking
        started = time.perf_counter()
        retrieval = retrieve(self._index, self._recipes, question, extraction)
        trace["stages_ms"]["retrieve"] = round((time.perf_counter() - started) * 1000, 1)
        trace["eligible_count"] = retrieval.eligible_count
        trace["rejected"] = retrieval.rejected
        trace["top_k"] = [{"id": h.recipe.id, "score": round(h.score, 3)} for h in retrieval.hits]
        if retrieval.eligible_count == 0:
            # Constraints are never relaxed to find a match (SPEC §4).
            return Outcome(None, [], "out_of_corpus", unmet_constraints_message(extraction))
        if not retrieval.hits:
            return Outcome(None, [], "out_of_corpus")
        context = [h.recipe for h in retrieval.hits[:CONTEXT_K]]
        allowed = {r.id for r in context}

        # 7-8. generation, validated against the candidate set
        generation = await self._call_once_retry(
            "generate",
            lambda: self.provider.generate(question, extraction, context),
            trace,
            validate=lambda g: _validated(g, allowed),
        )
        if not generation.answerable:
            return Outcome(None, [], "out_of_corpus")
        answer = (generation.answer or "").strip()
        return Outcome(answer, list(dict.fromkeys(generation.source_ids)))

    async def _call_once_retry[T](
        self,
        stage: str,
        call: Callable[[], Awaitable[Completion[T]]],
        trace: dict[str, Any],
        validate: Callable[[T], T] | None = None,
    ) -> T:
        """Run a model call; retry once on unusable output (SPEC §2.3).

        Usage is recorded for every completion the provider returned, before
        our own validation, because a rejected answer was still billed.
        """
        started = time.perf_counter()
        try:
            for attempt in (1, 2):
                try:
                    completion = await call()
                    trace["usage"].append(
                        {
                            "stage": stage,
                            "attempt": attempt,
                            "input_tokens": completion.usage.input_tokens,
                            "output_tokens": completion.usage.output_tokens,
                        }
                    )
                    return validate(completion.value) if validate else completion.value
                except ProviderInvalidOutput as exc:
                    trace.setdefault("invalid_output", []).append(f"{stage}#{attempt}: {exc}")
            raise UpstreamInvalid(f"{stage}: invalid output twice")
        except ProviderUnavailable as exc:
            raise UpstreamUnavailable(f"{stage}: {exc}") from exc
        finally:
            trace["stages_ms"][stage] = round((time.perf_counter() - started) * 1000, 1)


def _validated(generation: Generation, allowed: set[str]) -> Generation:
    """SPEC §3 step 8: an answer is non-empty and cites 1-3 given recipes."""
    if not generation.answerable:
        return generation
    if not (generation.answer or "").strip():
        raise ProviderInvalidOutput("answerable but empty answer")
    cited = set(generation.source_ids)
    if not 1 <= len(cited) <= CONTEXT_K:
        raise ProviderInvalidOutput(f"{len(cited)} distinct source_ids")
    if not cited <= allowed:
        raise ProviderInvalidOutput(f"source_ids outside candidates: {sorted(cited - allowed)}")
    return generation
