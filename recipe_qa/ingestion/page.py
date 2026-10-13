"""Whole-page parsing: wikitext → summary fields, ingredients, steps, sections."""

from dataclasses import dataclass

from recipe_qa.corpus import Ingredient


@dataclass(frozen=True)
class ParsedPage:
    summary_time: str | None
    summary_servings: str | None
    summary_difficulty: str | None
    categories: list[str]
    ingredients: list[Ingredient]
    steps: list[str]
    sections: dict[str, str]


def parse_page(wikitext: str) -> ParsedPage:
    raise NotImplementedError
