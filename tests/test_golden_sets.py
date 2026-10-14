"""The committed golden, held-out and extraction sets fit the corpus they claim to.

A golden case is only as good as its scenario: a "time constraint" case
whose competitors also pass the limit tests nothing. These tests check each
scenario against data/ (SPEC §7.2, evals/README.md), so a corpus change
that breaks a case fails here, not silently in a live run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from evals.checks import constraint_failures
from evals.sets import load_e2e_set, load_extraction_set
from recipe_qa.corpus import load_recipes

ROOT = Path(__file__).resolve().parent.parent
RECIPES = {r.id: r for r in load_recipes(ROOT / "data" / "recipes.jsonl")}
GOLDEN = load_e2e_set("golden")
HOLDOUT = load_e2e_set("holdout")
CASES = {c["id"]: c for c in GOLDEN.cases}


def page_text(recipe_id: str) -> str:
    """The page's visible text as ingested (markup removed, SPEC §6), not the wikitext,
    where a link can split a phrase: "risotto [[Cookbook:Rice|rice]]"."""
    recipe = RECIPES[recipe_id]
    return "\n".join(
        [i.raw for i in recipe.ingredients]
        + recipe.steps
        + list(recipe.sections.values())
        + [recipe.metadata.total_minutes.evidence or ""]
    )


def satisfying(constraints: dict) -> list[str]:
    return [rid for rid, r in RECIPES.items() if not constraint_failures(r, constraints)]


def test_golden_covers_the_fifteen_scenarios_once() -> None:
    assert not GOLDEN.held_out
    assert sorted(c["scenario"] for c in GOLDEN.cases) == list(range(1, 16))
    assert all("refused" in c["expect"] for c in GOLDEN.cases)


def test_holdout_paraphrases_five_golden_intents() -> None:
    assert HOLDOUT.held_out
    assert len(HOLDOUT.cases) == 5
    for case in HOLDOUT.cases:
        intent = CASES[case["paraphrases"]]
        assert case["question"].casefold() != intent["question"].casefold()
        assert case["expect"]["refused"] == intent["expect"]["refused"]


def test_extraction_set_labels_every_golden_question_and_ten_more() -> None:
    labels = {c["id"]: c["question"] for c in load_extraction_set("golden").cases}
    for case in GOLDEN.cases:
        assert labels.get(case["id"]) == case["question"]
    assert len(labels) - len(GOLDEN.cases) >= 10


@pytest.mark.parametrize("case", GOLDEN.cases + HOLDOUT.cases, ids=lambda c: c["id"])
def test_every_case_names_real_recipes(case: dict) -> None:
    named = case["expect"].get("sources_any", []) + case.get("competitors", [])
    assert set(named) <= set(RECIPES)


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN.cases + HOLDOUT.cases if c["expect"].get("expected_facts")],
    ids=lambda c: c["id"],
)
def test_expected_facts_are_on_a_source_page(case: dict) -> None:
    pages = [page_text(rid) for rid in case["expect"]["sources_any"]]
    for fact in case["expect"]["expected_facts"]:
        assert any(re.search(fact, page, re.IGNORECASE) for page in pages), fact


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN.cases + HOLDOUT.cases if c["expect"].get("forbidden_claims")],
    ids=lambda c: c["id"],
)
def test_forbidden_claims_are_on_no_source_page(case: dict) -> None:
    """A claim the source states, even as a variation, is not forbidden to repeat."""
    for rid in case["expect"]["sources_any"]:
        for claim in case["expect"]["forbidden_claims"]:
            assert not re.search(claim, page_text(rid), re.IGNORECASE), (rid, claim)


@pytest.mark.parametrize(
    "case",
    [c for c in GOLDEN.cases if c["expect"].get("constraints")],
    ids=lambda c: c["id"],
)
def test_constraint_scenarios_fit_the_corpus(case: dict) -> None:
    constraints = case["expect"]["constraints"]
    for rid in case["expect"].get("sources_any", []):
        assert not constraint_failures(RECIPES[rid], constraints), rid
    for rid in case.get("competitors", []):
        assert constraint_failures(RECIPES[rid], constraints), rid
    expected = [] if case["expect"]["refused"] else case["expect"]["sources_any"]
    assert set(expected) <= set(satisfying(constraints))


def test_time_case_has_a_competitor_at_the_limit_and_one_with_unknown_time() -> None:
    case = CASES["g07-under-n-minutes"]
    limit = case["expect"]["constraints"]["max_total_minutes"]
    times = [RECIPES[rid].metadata.total_minutes for rid in case["competitors"]]
    # Exactly at the limit: the competitor that tells "under" (<) from "within" (≤).
    assert any(t.status == "explicit" and t.value == limit for t in times)
    assert any(t.status == "unknown" for t in times)
    for rid in case["expect"]["sources_any"]:
        assert RECIPES[rid].metadata.total_minutes.status == "explicit"


def test_excluded_ingredient_is_only_optional_in_a_competitor() -> None:
    case = CASES["g09-excluded-optional"]
    words = case["expect"]["constraints"]["must_exclude"][0]
    pattern = re.compile(rf"\b({'|'.join(words)})s?\b", re.IGNORECASE)

    def matching(rid: str) -> list[bool]:
        return [i.optional for i in RECIPES[rid].ingredients if pattern.search(i.raw)]

    assert any(matching(rid) and all(matching(rid)) for rid in case["competitors"])


def test_compound_case_is_satisfied_by_exactly_one_recipe() -> None:
    case = CASES["g10-compound"]
    assert satisfying(case["expect"]["constraints"]) == case["expect"]["sources_any"]


def test_unsatisfiable_case_has_no_recipe_in_the_corpus() -> None:
    assert satisfying(CASES["g11-unsatisfiable"]["expect"]["constraints"]) == []
