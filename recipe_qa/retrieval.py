"""BM25 over whole recipes with field weights (ADR 001).

Field weighting is done by term-frequency multiplication (title x3,
ingredients x2, everything else x1), a simplified BM25F that is enough for
a few dozen documents. The index is built in memory at startup from the
committed snapshot; IDF is computed over the whole corpus, so restricting
a search to eligible candidates does not change a recipe's score.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from recipe_qa.corpus import Recipe

K1 = 1.2
B = 0.75
TITLE_WEIGHT = 3
INGREDIENT_WEIGHT = 2

# Question words and glue; food words are never stopwords.
_STOPWORD_TEXT = """
a about all also am an and any are as at be been but by can could did do does
doing for from get give got had has have how if in into is it its just like
me my need of on one or our please should show so some something tell than
that the their them then there these they this those to us use want was we
what when where which who why will with without would you your
"""
STOPWORDS = frozenset(_STOPWORD_TEXT.split())

_TOKEN = re.compile(r"[a-z0-9]+")


def _singular(word: str) -> str:
    if len(word) <= 3 or word.endswith(("ss", "us", "is")):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("s"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lower-case, accent-free, singular word tokens; no stopwords or single letters."""
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return [_singular(t) for t in _TOKEN.findall(folded) if len(t) > 1 and t not in STOPWORDS]


def recipe_terms(recipe: Recipe) -> Counter[str]:
    """Weighted term frequencies of one recipe."""
    terms: Counter[str] = Counter()
    for token in tokenize(recipe.title):
        terms[token] += TITLE_WEIGHT
    for ingredient in recipe.ingredients:
        for token in tokenize(ingredient.raw):
            terms[token] += INGREDIENT_WEIGHT
    body = [*recipe.steps, *recipe.sections.values(), *recipe.categories]
    for token in tokenize(" ".join(body)):
        terms[token] += 1
    return terms


@dataclass(frozen=True)
class Hit:
    recipe: Recipe
    score: float


class BM25Index:
    def __init__(self, recipes: Iterable[Recipe]) -> None:
        self._recipes = {r.id: r for r in recipes}
        self._terms = {rid: recipe_terms(r) for rid, r in self._recipes.items()}
        self._length = {rid: sum(t.values()) for rid, t in self._terms.items()}
        self._avg_length = sum(self._length.values()) / max(len(self._length), 1)
        df: Counter[str] = Counter()
        for terms in self._terms.values():
            df.update(terms.keys())
        n = len(self._terms)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query_terms: Iterable[str], recipe_id: str) -> float:
        terms = self._terms[recipe_id]
        norm = K1 * (1 - B + B * self._length[recipe_id] / self._avg_length)
        total = 0.0
        for term in query_terms:
            tf = terms.get(term, 0)
            if tf:
                total += self._idf[term] * tf * (K1 + 1) / (tf + norm)
        return total

    def search(
        self, query: str, candidate_ids: Iterable[str] | None = None, k: int = 5
    ) -> list[Hit]:
        """Top-k candidates with a positive score, best first, ties by ID.

        A recipe that shares no term with the query is never returned:
        an empty result means "nothing in the corpus matches".
        """
        query_terms = set(tokenize(query))
        if not query_terms:
            return []
        ids = self._recipes.keys() if candidate_ids is None else candidate_ids
        hits = [
            Hit(self._recipes[rid], s)
            for rid in ids
            if rid in self._recipes and (s := self.score(query_terms, rid)) > 0
        ]
        hits.sort(key=lambda h: (-h.score, h.recipe.id))
        return hits[:k]
