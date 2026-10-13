"""Ingredient line → Ingredient(raw, normalized, optional).

`normalized` keeps the ingredient words, including alternatives ("guanciale
or pancetta") and meaning-bearing adjectives ("unsalted butter", "ground
cloves"). It drops, in order: parentheticals, everything after the first
comma (prep notes such as "chopped", "to taste"), "to taste" and optional
markers, then leading quantities, units, size words and prep verbs.
A unit word is never stripped when it is the last word ("6 cloves").

Matching (stage 4) runs on `normalized` for inclusion; exclusion should also
look at `raw`, which keeps parenthetical detail such as "any nuts, e.g.
pistachios".
"""

import re

from recipe_qa.corpus import Ingredient
from recipe_qa.ingestion.wikitext import to_plain

_FRACTIONS = "½¼¾⅓⅔⅛⅜⅝⅞"
_NUMBER = rf"(?:\d+(?:[.,/]\d+)?[{_FRACTIONS}]?|[{_FRACTIONS}])"
_QUANTITY = re.compile(rf"^{_NUMBER}(?:(?:–|-|—){_NUMBER})?(?:[a-z]{{1,2}})?$")
OPTIONAL_MARKER = re.compile(
    r"\boptional\b|\bif desired\b|\bif you like\b|\bif available\b|\bif using\b", re.IGNORECASE
)
_TO_TASTE = re.compile(r"\b(?:or\s+)?(?:to|according to) taste\b")
_UNITS = {
    "g", "gram", "grams", "gr", "kg", "kilogram", "kilograms", "mg",
    "ml", "milliliter", "milliliters", "millilitre", "millilitres", "l", "liter", "liters",
    "litre", "litres", "dl", "cl",
    "cup", "cups", "c", "tbsp", "tbs", "tbl", "tablespoon", "tablespoons",
    "tsp", "teaspoon", "teaspoons", "oz", "ounce", "ounces", "fl", "fl.", "floz",
    "lb", "lbs", "pound", "pounds", "pinch", "pinches", "dash", "dashes",
    "clove", "cloves", "can", "cans", "tin", "tins", "jar", "jars",
    "package", "packages", "packet", "packets", "pkg", "stick", "sticks",
    "slice", "slices", "piece", "pieces", "bunch", "bunches", "sprig", "sprigs",
    "handful", "handfuls", "head", "heads", "quart", "quarts", "qt",
    "pint", "pints", "pt", "gallon", "gallons", "inch", "inches", "cm",
    "sheet", "sheets", "drop", "drops", "knob", "knobs", "stalk", "stalks",
    "bottle", "bottles", "bag", "bags", "box", "boxes", "container", "containers",
}  # fmt: skip
_FILLERS = {
    "of", "a", "an", "about", "approx", "approx.", "approximately", "around", "some",
    "few", "heaped", "heaping", "level", "rounded", "scant", "generous",
    "large", "medium", "small", "x", "/", "or", "and", "to", "+",
}  # fmt: skip
_PREP_WORDS = {
    "chopped", "diced", "minced", "grated", "sliced", "crushed", "melted", "softened",
    "finely", "coarsely", "roughly", "thinly", "fresh", "freshly", "shredded", "peeled",
    "beaten", "sifted", "cubed",
}  # fmt: skip


def _strip_parentheticals(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, re.sub(r"\([^()]*\)|\[[^\[\]]*\]", " ", text)
    return text


def _is_leading_noise(token: str) -> bool:
    return bool(_QUANTITY.match(token)) or token in _UNITS or token in _FILLERS


def normalize_name(plain: str) -> str:
    text = _strip_parentheticals(plain.lower())
    text = re.sub(r"^\s*optional\s*:?", " ", text)
    text = text.split(",")[0]
    text = _TO_TASTE.sub(" ", text)
    text = OPTIONAL_MARKER.sub(" ", text)
    text = re.sub(r"\bfreshly[- ]", " ", text)

    tokens = text.split()
    while len(tokens) > 1 and (_is_leading_noise(tokens[0]) or tokens[0] in _PREP_WORDS):
        tokens.pop(0)
    name = " ".join(tokens)
    name = re.sub(r"^[^\w]+|[^\w]+$", "", name)
    return name or plain.lower().strip()


def parse_ingredient_line(wikitext: str, group: str | None = None) -> Ingredient:
    plain = to_plain(wikitext)
    return Ingredient(
        raw=plain,
        normalized=normalize_name(plain),
        optional=bool(OPTIONAL_MARKER.search(plain)),
        group=group,
    )
