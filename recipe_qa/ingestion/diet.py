"""Diet metadata with provenance (SPEC §4).

The ingredient check always runs; an explicit category only decides what
the check cannot (SPEC §4 v0.2).

vegetarian: any ingredient matching MEAT_AND_FISH → False (derived, even
    against a "Vegetarian recipes"/"Vegan recipes" category, which the
    evidence names). Else explicit category → True. Else unqualified
    stock/broth or no parsed ingredients → unknown; else True (derived).
    Optional ingredients count.
vegan: vegetarian False → False. Any ingredient matching ANIMAL_PRODUCTS →
    False (derived, even against "Vegan recipes"). Else explicit category →
    True. Else follows vegetarian when that is unknown; else True (derived).
gluten_free: explicit category "Gluten-free recipes" only, else unknown.
    Hidden gluten (soy sauce, stock cubes, oats) is too easy to miss.

Matching runs on raw + normalized text, lower-cased, on word boundaries,
so "1 cup stock (chicken or beef)" is caught even though `normalized` is
"stock". A stock/broth counts as qualified only when the qualifier modifies
it directly ("vegetable stock", "stock (vegetable)"). A match preceded by
a plant qualifier ("coconut milk", "vegan sausage") or followed by a known
non-animal noun ("butter beans", "oyster mushrooms", "cream of tartar") is
ignored.
"""

import re

from recipe_qa.corpus import Diet, Fact, Ingredient

VEGETARIAN_CATEGORIES = ("Vegan recipes", "Vegetarian recipes")
VEGAN_CATEGORY = "Vegan recipes"
GLUTEN_FREE_CATEGORY = "Gluten-free recipes"

MEAT_AND_FISH = (
    "beef", "veal", "pork", "bacon", "ham", "prosciutto", "pancetta", "guanciale",
    "salami", "pepperoni", "chorizo", "sausage", "chicken", "turkey", "duck", "goose",
    "lamb", "mutton", "venison", "rabbit", "meat", "steak", "jerky", "foie",
    "lard", "suet", "gelatin", "gelatine",
    "anchovy", "anchovies", "fish", "salmon", "tuna", "cod", "haddock", "sardine",
    "mackerel", "trout", "tilapia", "catfish", "halibut", "shrimp", "prawn", "crab",
    "lobster", "clam", "mussel", "oyster", "squid", "octopus", "scallop", "crayfish",
    "crawfish", "caviar", "roe", "worcestershire", "dashi", "bonito",
)  # fmt: skip
AMBIGUOUS_STOCK = ("stock", "broth", "bouillon", "consommé", "consomme")
STOCK_QUALIFIERS = ("vegetable", "veggie", "vegan", "vegetarian", "mushroom", "kombu")
ANIMAL_PRODUCTS = (
    "milk", "buttermilk", "butter", "cream", "cheese", "yogurt", "yoghurt", "ghee",
    "egg", "eggs", "yolk", "honey", "mayonnaise", "mayo", "paneer", "curd", "whey",
    "casein", "custard", "kefir", "ricotta", "mozzarella", "parmesan", "parmigiano",
    "pecorino", "feta", "mascarpone", "cheddar", "gruyere", "gruyère", "halloumi",
    "crème", "creme", "dahi",
)  # fmt: skip

PLANT_BEFORE = re.compile(
    r"(coconut|almond|soy|soya|oat|rice|cashew|peanut|cocoa|shea|apple|nut|plant|"
    r"plant-based|vegan|vegetarian|veggie|tofu|meatless|imitation|mock|non-dairy|"
    r"dairy-free)[\s-]*$"
)
NOT_ANIMAL_AFTER = re.compile(r"^(?:s|es)?\s*(of tartar|beans?|mushrooms?)\b")


def _terms(words: tuple[str, ...]) -> re.Pattern[str]:
    alternatives = "|".join(sorted((re.escape(w) for w in words), key=len, reverse=True))
    return re.compile(rf"\b({alternatives})(?:s|es)?\b")


_QUALIFIERS = "|".join(STOCK_QUALIFIERS)
_QUALIFIER_BEFORE = re.compile(rf"\b({_QUALIFIERS})[\s-]+$")
_QUALIFIER_AFTER = re.compile(rf"^\s*\(\s*({_QUALIFIERS})\b")
_MEAT = _terms(MEAT_AND_FISH)
_ANIMAL = _terms(ANIMAL_PRODUCTS)
_STOCK = _terms(AMBIGUOUS_STOCK)


def _text(ingredient: Ingredient) -> str:
    return f"{ingredient.raw} {ingredient.normalized}".lower()


def _first_match(pattern: re.Pattern[str], ingredients: list[Ingredient]) -> str | None:
    """Evidence string for the first real match, or None."""
    for ingredient in ingredients:
        text = _text(ingredient)
        for match in pattern.finditer(text):
            before, after = text[: match.start()], text[match.start() + len(match[1]) :]
            if PLANT_BEFORE.search(before) or NOT_ANIMAL_AFTER.match(after):
                continue
            return f"ingredient '{ingredient.raw}' matches '{match[1]}'"
    return None


def _stock_is_qualified(text: str, match: re.Match[str]) -> bool:
    """The qualifier must modify this stock: "vegetable stock", "stock (vegetable)".

    A qualifier elsewhere in the line ("vegetable oil and stock") does not count.
    """
    return bool(
        _QUALIFIER_BEFORE.search(text[: match.start()])
        or _QUALIFIER_AFTER.match(text[match.end() :])
    )


def _ambiguous_stock(ingredients: list[Ingredient]) -> str | None:
    for ingredient in ingredients:
        text = _text(ingredient)
        if any(not _stock_is_qualified(text, m) for m in _STOCK.finditer(text)):
            return f"ingredient '{ingredient.raw}' is stock/broth of unstated origin"
    return None


def _explicit(categories: list[str], names: tuple[str, ...]) -> str | None:
    return next((f"Category:{c}" for c in names if c in categories), None)


def _overriding(evidence: str, category: str | None) -> str:
    return f"{evidence}; overrides {category}" if category else evidence


def _vegetarian(categories: list[str], ingredients: list[Ingredient]) -> Fact[bool]:
    category = _explicit(categories, VEGETARIAN_CATEGORIES)
    if meat := _first_match(_MEAT, ingredients):
        return Fact(value=False, status="derived", evidence=_overriding(meat, category))
    if category:
        return Fact(value=True, status="explicit", evidence=category)
    if not ingredients:
        return Fact.unknown("no ingredient list parsed")
    if stock := _ambiguous_stock(ingredients):
        return Fact.unknown(stock)
    return Fact(
        value=True,
        status="derived",
        evidence=f"none of {len(ingredients)} ingredients matches the meat/fish list",
    )


def _vegan(
    categories: list[str], ingredients: list[Ingredient], vegetarian: Fact[bool]
) -> Fact[bool]:
    category = _explicit(categories, (VEGAN_CATEGORY,))
    if vegetarian.value is False:
        return Fact(value=False, status="derived", evidence=vegetarian.evidence)
    if animal := _first_match(_ANIMAL, ingredients):
        return Fact(value=False, status="derived", evidence=_overriding(animal, category))
    if category:
        return Fact(value=True, status="explicit", evidence=category)
    if vegetarian.status == "unknown":
        return Fact.unknown(vegetarian.evidence)
    return Fact(
        value=True,
        status="derived",
        evidence=f"none of {len(ingredients)} ingredients matches the meat/fish "
        "or dairy/egg/honey lists",
    )


def derive_diet(categories: list[str], ingredients: list[Ingredient]) -> Diet:
    vegetarian = _vegetarian(categories, ingredients)
    gluten_free = (
        Fact(value=True, status="explicit", evidence=f"Category:{GLUTEN_FREE_CATEGORY}")
        if GLUTEN_FREE_CATEGORY in categories
        else Fact.unknown("not in an explicit gluten-free category; never derived")
    )
    return Diet(
        vegetarian=vegetarian,
        vegan=_vegan(categories, ingredients, vegetarian),
        gluten_free=gluten_free,
    )


def category_conflicts(categories: list[str], ingredients: list[Ingredient]) -> list[str]:
    """Explicit diet categories that the ingredient list contradicts.

    Wikibooks categories are user-maintained: about 6 % of pages in the
    vegetarian/vegan categories list meat, dairy or honey. derive_diet()
    already overrides such a category; selection also uses this to exclude
    these pages as low-quality data.
    """
    derived = derive_diet([], ingredients)
    conflicts = []
    if any(c in categories for c in VEGETARIAN_CATEGORIES) and derived.vegetarian.value is False:
        conflicts.append(f"vegetarian category, but {derived.vegetarian.evidence}")
    if VEGAN_CATEGORY in categories and derived.vegan.value is False:
        conflicts.append(f"vegan category, but {derived.vegan.evidence}")
    return conflicts
