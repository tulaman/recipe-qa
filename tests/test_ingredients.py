"""Ingredient line → {raw, normalized, optional} (SPEC §4, §6).

Inputs are wikitext lines (without the leading `*`) copied from real
Wikibooks pages. `normalized` is what constraint matching runs on, so it
must keep the ingredient words and drop quantities, units and prep notes.
"""

import pytest

from recipe_qa.ingestion.ingredients import parse_ingredient_line
from recipe_qa.ingestion.wikitext import to_plain


@pytest.mark.parametrize(
    ("wikitext", "plain"),
    [
        (
            "450 [[Cookbook:Gram|g]] (1 [[Cookbook:Pound|pound]]) [[Cookbook:Pasta|spaghetti]]",
            "450 g (1 pound) spaghetti",
        ),
        ("{{convert|450|g|lb}} [[Cookbook:Flour|flour]]", "450 g flour"),
        ("1 [[Cookbook:Cup|cup]] sugar<ref>See note.</ref>", "1 cup sugar"),
        (
            "¼ [[cookbook:tsp|tsp]] [[wikt:ground|ground]] [[cookbook:clove|cloves]]",
            "¼ tsp ground cloves",
        ),
        ("''Fresh'' basil &amp; mint", "Fresh basil & mint"),
        ("[[File:Basil.jpg|thumb|Basil]] basil", "basil"),
        ("[[Cookbook:Salt|Salt]]", "Salt"),
    ],
)
def test_to_plain_strips_markup_and_keeps_visible_text(wikitext: str, plain: str) -> None:
    assert to_plain(wikitext) == plain


@pytest.mark.parametrize(
    ("wikitext", "normalized"),
    [
        (
            "450 [[Cookbook:Gram|g]] (1 [[Cookbook:Pound|pound]]) [[Cookbook:Pasta|spaghetti]]",
            "spaghetti",
        ),
        (
            "225–500 g (½–1 pound) guanciale or [[Cookbook:Pancetta|pancetta]]",
            "guanciale or pancetta",
        ),
        ("5 [[Cookbook:Egg yolk|egg yolks]]", "egg yolks"),
        (
            "178 [[Cookbook:Milliliter|ml]] (¾ [[Cookbook:Cup|cup]]) grated "
            "[[Cookbook:Pecorino Romano Cheese|Pecorino Romano]] cheese",
            "pecorino romano cheese",
        ),
        ("½ tablespoon freshly-ground [[Cookbook:Pepper|pepper]]", "ground pepper"),
        ("[[Cookbook:Salt|Salt]]", "salt"),
        ("[[Cookbook:Cinnamon|Cinnamon]], to taste", "cinnamon"),
        ("salt to taste", "salt"),
        (
            "4–5 [[cookbook:cup|cup]] [[cookbook:walnuts|walnuts]], [[Cookbook:Chopping|chopped]] coarsely",
            "walnuts",
        ),
        ("2 ½ cups of [[cookbook:water|water]]", "water"),
        ("1¼ lb unsalted [[Cookbook:Butter|butter]], melted", "unsalted butter"),
        (
            "2 [[Cookbook:Cinnamon|cinnamon]] sticks, each 3 [[Cookbook:Inch|inches]] long",
            "cinnamon sticks",
        ),
        ("4 cloves garlic, minced", "garlic"),
        ("¼ tsp ground cloves", "ground cloves"),
        ("A pinch of [[Cookbook:Salt|salt]]", "salt"),
        ("1 (14 oz) can diced tomatoes", "tomatoes"),
        ("2 large [[Cookbook:Onion|onions]], finely chopped", "onions"),
        ("1 lb (450 g) nuts (any type, but preferrably pistachios or walnuts)", "nuts"),
        ("{{convert|200|g|oz}} plain [[Cookbook:Flour|flour]]", "plain flour"),
    ],
)
def test_normalized_name_drops_quantity_unit_and_prep(wikitext: str, normalized: str) -> None:
    assert parse_ingredient_line(wikitext).normalized == normalized


@pytest.mark.parametrize(
    "wikitext",
    [
        "⅛ [[Cookbook:Teaspoon|tsp]] ground [[Cookbook:Cardamom|cardamom]] or to taste (optional)",
        "220 g (1 cup / 8 oz) [[Cookbook:Raisin|raisins]] or Zante currants, if desired",
        "Optional: chopped parsley",
        "1 tbsp butter (if you like)",
    ],
)
def test_optional_markers_are_detected(wikitext: str) -> None:
    assert parse_ingredient_line(wikitext).optional is True


def test_optional_marker_is_removed_from_normalized_name() -> None:
    line = (
        "⅛ [[Cookbook:Teaspoon|tsp]] ground [[Cookbook:Cardamom|cardamom]] or to taste (optional)"
    )
    assert parse_ingredient_line(line).normalized == "ground cardamom"


@pytest.mark.parametrize(
    "wikitext",
    ["[[Cookbook:Salt|Salt]], to taste", "4 eggs", "Oil for frying"],
)
def test_plain_lines_are_not_optional(wikitext: str) -> None:
    assert parse_ingredient_line(wikitext).optional is False


def test_raw_is_display_text_not_wikitext() -> None:
    line = "1 lb (450 g) [[Cookbook:Nuts and Seeds|nuts]] (any type, but preferrably [[Cookbook:Pistachio|pistachios]])"
    assert (
        parse_ingredient_line(line).raw
        == "1 lb (450 g) nuts (any type, but preferrably pistachios)"
    )
