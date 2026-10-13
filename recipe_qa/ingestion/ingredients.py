"""Ingredient line normalization."""

from recipe_qa.corpus import Ingredient


def parse_ingredient_line(wikitext: str, group: str | None = None) -> Ingredient:
    raise NotImplementedError
