"""Diet metadata with provenance."""

from recipe_qa.corpus import Diet, Ingredient


def derive_diet(categories: list[str], ingredients: list[Ingredient]) -> Diet:
    raise NotImplementedError
