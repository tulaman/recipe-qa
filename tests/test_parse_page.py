"""Whole-page parsing on real and synthetic Wikibooks pages (SPEC §6).

Fixtures in tests/fixtures/ are pinned revisions:
- carbonara.wiki: Cookbook:Spaghetti alla Carbonara, revid 4640105 —
  structured page, explicit time, flat ingredient list.
- baklava_i.wiki: Cookbook:Baklava I, revid 4617976 — messy page, no time,
  ingredient and procedure sub-sections.
- rice_pudding.wiki: Cookbook:Rice Pudding, revid 4522360 — no time,
  optional ingredients ("if desired", "(optional)").
"""

from pathlib import Path

import pytest

from recipe_qa.ingestion.page import ParsedPage, parse_page

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> ParsedPage:
    return parse_page((FIXTURES / f"{name}.wiki").read_text(encoding="utf-8"))


def test_structured_page_summary_fields() -> None:
    page = load("carbonara")
    assert page.summary_time == "1 hour"
    assert page.summary_servings == "6"
    assert page.summary_difficulty == "2"


def test_structured_page_ingredients_and_steps() -> None:
    page = load("carbonara")
    names = [i.normalized for i in page.ingredients]
    assert names == [
        "spaghetti",
        "guanciale or pancetta",
        "egg yolks",
        "pecorino romano cheese",
        "parmigiano-reggiano cheese",
        "extra-virgin olive oil",
        "ground pepper",
        "salt",
    ]
    assert all(i.group is None for i in page.ingredients)
    assert len(page.steps) == 8
    assert page.steps[0].startswith("Dice the guanciale or pancetta")
    assert "[[" not in "".join(page.steps)


def test_categories_come_from_the_pinned_wikitext() -> None:
    page = load("carbonara")
    assert "Italian recipes" in page.categories
    assert len(page.categories) == len(set(page.categories))
    assert all(not c.startswith("Category:") for c in page.categories)


def test_other_sections_are_kept_as_plain_text() -> None:
    page = load("carbonara")
    assert "description" in page.sections
    assert page.sections["description"].startswith("Spaghetti alla carbonara")
    assert "notes, tips, and variations" in page.sections
    assert "equipment" in page.sections
    # References and navigation sections carry no recipe content.
    assert "references" not in page.sections
    assert "see also" not in page.sections


def test_messy_page_keeps_ingredient_groups() -> None:
    page = load("baklava_i")
    assert page.summary_time is None
    groups = {i.group for i in page.ingredients}
    assert groups == {"Pastry", "Syrup"}
    walnuts = next(i for i in page.ingredients if i.normalized == "walnuts")
    assert walnuts.group == "Pastry"


def test_messy_page_prefixes_steps_with_their_subsection() -> None:
    page = load("baklava_i")
    assert page.steps[0].startswith("Pastry: Combine nuts")
    assert page.steps[-1].startswith("Syrup: Pour cooled syrup")


def test_optional_ingredients_on_real_page() -> None:
    page = load("rice_pudding")
    optional = {i.normalized for i in page.ingredients if i.optional}
    assert optional == {"raisins or zante currants", "ground cardamom"}


SYNTHETIC = """\
{{Recipe summary
| category = Soup recipes
| Time = Prep: 10 minutes<br/>Cooking: 20 minutes
| servings = 4
}}
{{recipe}}
A quick soup.<ref>Some source.</ref>

== Ingredients ==
* 1 onion
* 2 cups vegetable [[Cookbook:Stock|stock]]
* Optional garnish:
** fresh [[Cookbook:Parsley|parsley]]

== Preparation ==
Heat the oil and fry the onion.

Add the stock and simmer for 20 minutes.

== Procedure ==
# Fry the onion.
# Add the stock.
## Simmer for 20 minutes.

[[Category:Soup recipes]]
[[Category:Vegan recipes|Soup]]
[[Category:Soup recipes]]
"""


def test_synthetic_page_handles_template_case_and_br_in_time() -> None:
    page = parse_page(SYNTHETIC)
    assert page.summary_time == "Prep: 10 minutes<br/>Cooking: 20 minutes"
    assert page.summary_servings == "4"
    assert page.summary_difficulty is None


def test_synthetic_page_prefers_procedure_over_preparation() -> None:
    page = parse_page(SYNTHETIC)
    assert page.steps == ["Fry the onion.", "Add the stock.", "Simmer for 20 minutes."]


def test_item_ending_with_colon_labels_its_nested_items() -> None:
    page = parse_page(SYNTHETIC)
    assert [i.normalized for i in page.ingredients] == ["onion", "vegetable stock", "parsley"]
    parsley = page.ingredients[-1]
    assert parsley.group == "Optional garnish"
    assert parsley.optional is True


def test_categories_are_deduplicated_and_sort_keys_dropped() -> None:
    assert parse_page(SYNTHETIC).categories == ["Soup recipes", "Vegan recipes"]


def test_description_drops_templates_and_refs() -> None:
    assert parse_page(SYNTHETIC).sections["description"] == "A quick soup."


def test_prose_procedure_becomes_paragraph_steps() -> None:
    text = "== Ingredients ==\n* 1 egg\n\n== Method ==\nBoil the egg.\n\nPeel it.\n"
    assert parse_page(text).steps == ["Boil the egg.", "Peel it."]


@pytest.mark.parametrize("text", ["", "{{Cookdp}}\n* [[Cookbook:Baklava I]]\n"])
def test_page_without_recipe_sections_parses_to_empty(text: str) -> None:
    page = parse_page(text)
    assert page.ingredients == []
    assert page.steps == []


def test_summary_yield_is_read() -> None:
    page = parse_page("{{Recipe summary\n| Servings = \n| Yield = 4 pancakes\n}}\n")
    assert page.summary_servings is None
    assert page.summary_yield == "4 pancakes"
