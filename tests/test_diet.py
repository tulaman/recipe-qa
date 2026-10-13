"""Diet metadata with provenance (SPEC §4 eligibility table).

explicit: the page is in a diet category. derived: a documented ingredient
list decided it, and the evidence names the ingredient. unknown: the
derived check cannot decide (unqualified stock/broth) or the diet is
gluten-free, which is never derived.
"""

import pytest

from recipe_qa.corpus import Ingredient
from recipe_qa.ingestion.diet import category_conflicts, derive_diet


def ing(*names: str) -> list[Ingredient]:
    return [Ingredient(raw=n, normalized=n.lower(), optional=False) for n in names]


def test_explicit_vegetarian_category() -> None:
    diet = derive_diet(["Vegetarian recipes"], ing("eggs", "milk"))
    assert (diet.vegetarian.value, diet.vegetarian.status) == (True, "explicit")
    assert diet.vegetarian.evidence == "Category:Vegetarian recipes"


def test_explicit_vegan_category_implies_vegetarian() -> None:
    diet = derive_diet(["Vegan recipes"], ing("rice"))
    assert (diet.vegan.value, diet.vegan.status) == (True, "explicit")
    assert (diet.vegetarian.value, diet.vegetarian.status) == (True, "explicit")


@pytest.mark.parametrize(
    ("ingredient", "matched"),
    [
        ("guanciale or pancetta", "guanciale"),
        ("bacon", "bacon"),
        ("2 chicken breasts", "chicken"),
        ("chicken stock", "chicken"),
        ("anchovies", "anchovies"),
        ("prawns", "prawns"),
        ("unflavored gelatin", "gelatin"),
        ("lard", "lard"),
        ("fish sauce", "fish"),
        ("worcestershire sauce", "worcestershire"),
    ],
)
def test_meat_or_fish_ingredient_derives_not_vegetarian(ingredient: str, matched: str) -> None:
    diet = derive_diet([], ing("onion", ingredient))
    assert (diet.vegetarian.value, diet.vegetarian.status) == (False, "derived")
    assert matched in (diet.vegetarian.evidence or "")
    assert (diet.vegan.value, diet.vegan.status) == (False, "derived")


def test_meat_found_in_raw_text_counts_even_if_normalized_drops_it() -> None:
    ingredients = [
        Ingredient(raw="1 cup stock (chicken or beef)", normalized="stock", optional=False)
    ]
    assert derive_diet([], ingredients).vegetarian.value is False


def test_optional_meat_still_counts() -> None:
    ingredients = [Ingredient(raw="bacon bits, optional", normalized="bacon bits", optional=True)]
    assert derive_diet([], ingredients).vegetarian.value is False


@pytest.mark.parametrize("ingredient", ["stock", "broth", "bouillon cube", "stock/broth"])
def test_unqualified_stock_is_unknown(ingredient: str) -> None:
    diet = derive_diet([], ing("onion", ingredient))
    assert (diet.vegetarian.value, diet.vegetarian.status) == (None, "unknown")
    assert ingredient in (diet.vegetarian.evidence or "")
    assert diet.vegan.status == "unknown"


def test_vegetable_stock_is_not_ambiguous() -> None:
    diet = derive_diet([], ing("onion", "vegetable stock"))
    assert (diet.vegetarian.value, diet.vegetarian.status) == (True, "derived")


def test_no_meat_derives_vegetarian() -> None:
    diet = derive_diet([], ing("eggplant", "olive oil", "garlic"))
    assert (diet.vegetarian.value, diet.vegetarian.status) == (True, "derived")
    assert diet.vegetarian.evidence


@pytest.mark.parametrize(
    ("ingredient", "matched"),
    [
        ("eggs", "eggs"),
        ("egg yolks", "egg"),
        ("unsalted butter", "butter"),
        ("whole milk", "milk"),
        ("parmesan cheese", "cheese"),
        ("honey", "honey"),
        ("ghee", "ghee"),
        ("plain yogurt", "yogurt"),
        ("heavy cream", "cream"),
    ],
)
def test_animal_product_derives_not_vegan(ingredient: str, matched: str) -> None:
    diet = derive_diet([], ing("rice", ingredient))
    assert diet.vegetarian.value is True
    assert (diet.vegan.value, diet.vegan.status) == (False, "derived")
    assert matched in (diet.vegan.evidence or "")


@pytest.mark.parametrize(
    "ingredient",
    [
        "coconut milk",
        "almond milk",
        "peanut butter",
        "cocoa butter",
        "cream of tartar",
        "eggplant",
        "vegan cheese",
        "butternut squash",
        "butter beans",
    ],
)
def test_plant_lookalikes_do_not_break_vegan(ingredient: str) -> None:
    diet = derive_diet([], ing("rice", ingredient))
    assert (diet.vegan.value, diet.vegan.status) == (True, "derived")


def test_explicit_vegetarian_with_dairy_derives_not_vegan() -> None:
    diet = derive_diet(["Vegetarian recipes"], ing("rice", "butter"))
    assert diet.vegetarian.status == "explicit"
    assert (diet.vegan.value, diet.vegan.status) == (False, "derived")


def test_gluten_free_is_explicit_category_only() -> None:
    assert derive_diet(["Gluten-free recipes"], ing("rice")).gluten_free.value is True
    diet = derive_diet([], ing("rice", "water"))
    assert (diet.gluten_free.value, diet.gluten_free.status) == (None, "unknown")


def test_no_ingredients_is_unknown() -> None:
    diet = derive_diet([], [])
    assert diet.vegetarian.status == "unknown"
    assert diet.vegan.status == "unknown"


def test_category_contradicted_by_ingredients_is_reported() -> None:
    conflicts = category_conflicts(["Vegetarian recipes"], ing("bread", "bacon"))
    assert len(conflicts) == 1
    assert "bacon" in conflicts[0]


def test_vegan_category_with_honey_is_reported() -> None:
    conflicts = category_conflicts(["Vegan recipes"], ing("oats", "honey"))
    assert conflicts == ["vegan category, but ingredient 'honey' matches 'honey'"]


def test_consistent_categories_report_nothing() -> None:
    assert category_conflicts(["Vegan recipes"], ing("rice", "coconut milk")) == []
    assert category_conflicts([], ing("bacon")) == []
