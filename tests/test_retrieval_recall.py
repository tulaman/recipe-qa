"""Retrieval recall@5 on the golden set, without generation (SPEC §7.1, §7.2).

Each answerable golden question is retrieved exactly as the service does
it (eligibility over the whole corpus, then BM25, k = 5), with the
constraints its extraction label says the question states. That isolates
retrieval: extraction is measured on its own set, generation end to end.
A case is recalled when one of its expected sources is in the top 5.
Target 100 %; a miss is an ADR 001 question, not something to tune here.
"""

from pathlib import Path

from evals.sets import load_e2e_set, load_extraction_set
from recipe_qa.corpus import load_recipes
from recipe_qa.retrieval import BM25Index
from recipe_qa.schemas import Extraction
from recipe_qa.service import RETRIEVE_K, retrieve

ROOT = Path(__file__).resolve().parent.parent


def test_recall_at_5_on_golden_answerable_cases() -> None:
    recipes = load_recipes(ROOT / "data" / "recipes.jsonl")
    index = BM25Index(recipes)
    labels: dict[str, Extraction] = {
        c["id"]: c["expect"] for c in load_extraction_set("golden").cases
    }
    answerable = [c for c in load_e2e_set("golden").cases if not c["expect"]["refused"]]

    misses = {}
    for case in answerable:
        hits = retrieve(index, recipes, case["question"], labels[case["id"]], k=RETRIEVE_K).hits
        top = [h.recipe.id for h in hits]
        if not set(top) & set(case["expect"]["sources_any"]):
            misses[case["id"]] = top

    recall = 1 - len(misses) / len(answerable)
    print(f"recall@{RETRIEVE_K}: {len(answerable) - len(misses)}/{len(answerable)} = {recall:.0%}")
    assert not misses, f"recall@{RETRIEVE_K} {recall:.0%}; top-5 of missed cases: {misses}"
