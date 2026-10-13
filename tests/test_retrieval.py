"""BM25 over whole recipes (ADR 001), on the committed corpus."""

from pathlib import Path

import pytest

from recipe_qa.corpus import Recipe, load_recipes
from recipe_qa.retrieval import BM25Index, tokenize

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="module")
def recipes() -> list[Recipe]:
    return load_recipes(DATA / "recipes.jsonl")


@pytest.fixture(scope="module")
def index(recipes: list[Recipe]) -> BM25Index:
    return BM25Index(recipes)


def ids(hits: list) -> list[str]:
    return [h.recipe.id for h in hits]


def test_tokenize_lowercases_drops_stopwords_and_plurals() -> None:
    assert tokenize("How do I make the Eggs and Tomatoes?") == ["make", "egg", "tomato"]
    assert tokenize("Crème brûlée") == ["creme", "brulee"]


def test_dish_name_ranks_that_dish_first(index: BM25Index) -> None:
    assert set(ids(index.search("How do I make baklava?"))[:2]) == {"baklava-i", "baklava-ii"}
    assert ids(index.search("guacamole recipe"))[0] in {"guacamole-i", "chunky-guacamole"}
    assert ids(index.search("kedgeree"))[0] == "kedgeree-rice-and-smoked-fish"


def test_overlapping_dishes_are_all_retrieved(index: BM25Index) -> None:
    top = ids(index.search("rice pudding", k=5))
    assert {"rice-pudding", "italian-rice-pudding", "kheer-rice-pudding"} <= set(top)


def test_ingredient_words_find_recipes(index: BM25Index) -> None:
    assert "carbonara-pasta" in ids(index.search("pasta with bacon and eggs"))


def test_search_is_restricted_to_candidates(index: BM25Index) -> None:
    hits = index.search("baklava", candidate_ids={"baklava-ii", "rice-pudding"})
    assert ids(hits) == ["baklava-ii"]


def test_no_term_overlap_returns_nothing(index: BM25Index) -> None:
    assert index.search("xylophone quantum") == []
    assert index.search("the and of") == []


def test_k_limits_results_and_order_is_deterministic(index: BM25Index) -> None:
    first = index.search("soup", k=3)
    assert len(first) == 3
    assert ids(first) == ids(index.search("soup", k=3))
    scores = [h.score for h in first]
    assert scores == sorted(scores, reverse=True)
