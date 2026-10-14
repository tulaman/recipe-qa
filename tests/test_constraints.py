"""is_eligible: one test per row of the SPEC §4 eligibility table.

Recipes are built here with exactly the metadata a row needs, so each test
states its premise; a few rows are also checked on the committed corpus,
where the premise is a real page.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from recipe_qa.constraints import ALIASES, describe, is_eligible, why_ineligible
from recipe_qa.corpus import Diet, Fact, Ingredient, Metadata, Recipe, load_recipes
from recipe_qa.schemas import Extraction

ROOT = Path(__file__).resolve().parent.parent
CORPUS = {r.id: r for r in load_recipes(ROOT / "data" / "recipes.jsonl")}

UNKNOWN = Fact.unknown()


def ing(raw: str, normalized: str | None = None, optional: bool = False) -> Ingredient:
    return Ingredient(raw=raw, normalized=normalized or raw.lower(), optional=optional)


def recipe(
    ingredients: list[Ingredient] | None = None,
    minutes: Fact[int] = UNKNOWN,
    vegetarian: Fact[bool] = UNKNOWN,
    vegan: Fact[bool] = UNKNOWN,
    gluten_free: Fact[bool] = UNKNOWN,
) -> Recipe:
    return Recipe(
        id="r",
        title="R",
        url="https://en.wikibooks.org/wiki/Cookbook:R",
        page_id=1,
        revid=1,
        fetched_at="2026-10-14T00:00:00Z",
        content_hash="0",
        categories=[],
        ingredients=ingredients or [ing("water")],
        steps=[],
        sections={},
        metadata=Metadata(
            total_minutes=minutes,
            servings=UNKNOWN,
            difficulty=UNKNOWN,
            diet=Diet(vegetarian=vegetarian, vegan=vegan, gluten_free=gluten_free),
        ),
    )


def ask(**constraints: Any) -> Extraction:
    return Extraction(in_domain=True, safety_question=False, **constraints)


def explicit(value: Any) -> Fact[Any]:
    return Fact(value=value, status="explicit", evidence="stated")


def derived(value: Any) -> Fact[Any]:
    return Fact(value=value, status="derived", evidence="rule")


# --- max_total_minutes ------------------------------------------------------------


def test_under_n_is_strict() -> None:
    assert is_eligible(recipe(minutes=explicit(19)), ask(max_total_minutes=20))
    assert not is_eligible(recipe(minutes=explicit(20)), ask(max_total_minutes=20))


def test_within_n_is_inclusive() -> None:
    within = ask(max_total_minutes=20, time_limit_inclusive=True)
    assert is_eligible(recipe(minutes=explicit(20)), within)
    assert not is_eligible(recipe(minutes=explicit(21)), within)


def test_derived_time_counts_as_known() -> None:
    assert is_eligible(recipe(minutes=derived(15)), ask(max_total_minutes=20))


@pytest.mark.parametrize("limit", [1, 30, 10_000])
@pytest.mark.parametrize("inclusive", [False, True])
def test_unknown_time_never_passes(limit: int, inclusive: bool) -> None:
    constraints = ask(max_total_minutes=limit, time_limit_inclusive=inclusive)
    assert not is_eligible(recipe(minutes=UNKNOWN), constraints)
    assert why_ineligible(recipe(minutes=UNKNOWN), constraints) == [
        f"total time unknown, need {'≤' if inclusive else '<'} {limit} min"
    ]


def test_range_uses_its_upper_bound() -> None:
    # Kheer states "30–60 minutes": it is a 60-minute recipe, never a 30-minute one.
    kheer = CORPUS["kheer-rice-pudding"]
    assert kheer.metadata.total_minutes.evidence == "30–60 minutes"
    assert not is_eligible(kheer, ask(max_total_minutes=45))
    assert not is_eligible(kheer, ask(max_total_minutes=60))
    assert is_eligible(kheer, ask(max_total_minutes=60, time_limit_inclusive=True))


# --- diet -------------------------------------------------------------------------


@pytest.mark.parametrize("diet", ["vegetarian", "vegan"])
@pytest.mark.parametrize(
    ("fact", "passes"),
    [
        (explicit(True), True),
        (derived(True), True),  # the derived check was conclusive
        (UNKNOWN, False),  # e.g. unqualified stock, no category vouching
        (derived(False), False),  # e.g. a contradicted category
    ],
)
def test_vegetarian_and_vegan_follow_the_diet_fact(
    diet: str, fact: Fact[bool], passes: bool
) -> None:
    assert is_eligible(recipe(**{diet: fact}), ask(diet=diet)) is passes


def test_a_contradicted_category_never_passes() -> None:
    contradicted = Fact(
        value=False,
        status="derived",
        evidence="ingredient 'chicken stock' matches 'chicken'; overrides Category:Vegan recipes",
    )
    assert not is_eligible(recipe(vegetarian=contradicted, vegan=contradicted), ask(diet="vegan"))


@pytest.mark.parametrize(
    ("fact", "passes"),
    [(explicit(True), True), (derived(True), False), (UNKNOWN, False)],
)
def test_gluten_free_needs_an_explicit_category(fact: Fact[bool], passes: bool) -> None:
    assert is_eligible(recipe(gluten_free=fact), ask(diet="gluten_free")) is passes


def test_gluten_free_is_not_derived_from_the_ingredient_list() -> None:
    # No gluten source on the list does not make a recipe gluten-free.
    assert not is_eligible(recipe([ing("rice"), ing("water")]), ask(diet="gluten_free"))


# --- must_include -----------------------------------------------------------------


def test_include_matches_a_normalized_ingredient() -> None:
    r = recipe([ing("2 cups black beans, drained", "black beans")])
    assert is_eligible(r, ask(must_include=["black bean"]))
    assert not is_eligible(r, ask(must_include=["potato"]))


def test_include_matches_through_an_alias() -> None:
    assert is_eligible(
        recipe([ing("1 cup dry chickpeas", "dry chickpeas")]), ask(must_include=["garbanzo bean"])
    )
    assert is_eligible(recipe([ing("full-fat yoghurt")]), ask(must_include=["yogurt"]))


def test_include_matches_whole_words_only() -> None:
    assert not is_eligible(recipe([ing("eggplant")]), ask(must_include=["egg"]))
    assert not is_eligible(recipe([ing("nutmeg")]), ask(must_include=["nut"]))


def test_include_ignores_raw_only_text() -> None:
    # SPEC §4 matches inclusion on the normalized name; a parenthetical aside
    # ("or use chicken") is not an ingredient of the recipe.
    r = recipe([ing("1 block tofu (or use chicken)", "tofu")])
    assert not is_eligible(r, ask(must_include=["chicken"]))


@pytest.mark.parametrize(
    ("ingredient", "term"),
    [
        ("bay leaves", "bay leaf"),
        ("2 loaves stale bread", "loaf"),
        ("kitchen knives", "knife"),
        ("smoked turkeys", "turkey"),
        ("fresh berries", "berry"),
    ],
)
def test_include_matches_irregular_plurals(ingredient: str, term: str) -> None:
    assert is_eligible(recipe([ing(ingredient)]), ask(must_include=[term]))


def test_bay_leaf_finds_the_recipe_that_lists_bay_leaves() -> None:
    r = CORPUS["italian-rice-pilaf"]
    assert any("bay leaves" in i.raw.lower() for i in r.ingredients)  # the premise
    assert is_eligible(r, ask(must_include=["bay leaf"]))
    assert not is_eligible(r, ask(must_exclude=["bay leaf"]))


def test_aliases_are_directional() -> None:
    assert is_eligible(recipe([ing("almonds")]), ask(must_include=["nut"]))
    assert not is_eligible(recipe([ing("walnuts")]), ask(must_include=["almond"]))


def test_gluten_sources_are_never_used_for_inclusion() -> None:
    assert not is_eligible(recipe([ing("flour")]), ask(must_include=["gluten"]))


def test_include_on_the_corpus_reaches_both_chickpea_recipes() -> None:
    garbanzo = ask(must_include=["garbanzo bean"])
    eligible = {rid for rid, r in CORPUS.items() if is_eligible(r, garbanzo)}
    assert eligible == {"spicy-chickpea-soup", "qabuli-central-asian-rice-pilaf"}


# --- must_exclude -----------------------------------------------------------------


def test_optional_ingredients_count_as_present() -> None:
    r = recipe([ing("raisins (optional)", "raisins", optional=True)])
    assert not is_eligible(r, ask(must_exclude=["raisin"]))
    assert why_ineligible(r, ask(must_exclude=["raisin"])) == [
        "contains 'raisins (optional)' (optional), excluded by 'raisin'"
    ]


def test_exclude_on_the_corpus_drops_the_optional_raisins() -> None:
    no_raisins = ask(must_include=["rice"], must_exclude=["raisin"])
    assert not is_eligible(CORPUS["rice-pudding"], no_raisins)  # "if desired"
    assert not is_eligible(CORPUS["kheer-rice-pudding"], no_raisins)  # sultanas
    assert is_eligible(CORPUS["italian-rice-pudding"], no_raisins)


@pytest.mark.parametrize(
    ("ingredient", "excluded"),
    [
        (ing("slivered blanched almonds"), "nut"),
        (ing("1 cup any nuts (e.g. pistachios)", "any nuts"), "nut"),
        (ing("2 tbsp mixed seeds (or pistachios)", "mixed seeds"), "nut"),
        (ing("sultanas"), "raisin"),
        (ing("heavy cream"), "dairy"),
        (ing("parmigiano-reggiano cheese"), "dairy"),
        (ing("2 egg yolks", "egg yolks"), "egg"),
        (ing("mayonnaise"), "egg"),
        (ing("2 tbsp crème fraîche"), "dairy"),
        (ing("Gruyère, grated"), "dairy"),
        (ing("all-purpose flour"), "gluten"),
        (ing("soy sauce"), "gluten"),
    ],
)
def test_exclude_matches_aliases_and_raw_text(ingredient: Ingredient, excluded: str) -> None:
    assert not is_eligible(recipe([ingredient]), ask(must_exclude=[excluded]))


@pytest.mark.parametrize(
    ("ingredient", "excluded"),
    [
        (ing("light coconut milk"), "dairy"),
        (ing("peanut butter"), "dairy"),
        (ing("butter beans"), "dairy"),
        (ing("cream of tartar"), "dairy"),
        (ing("nutmeg"), "nut"),
        (ing("grated coconut"), "nut"),
        (ing("eggplant"), "egg"),
    ],
)
def test_exclude_ignores_look_alikes(ingredient: Ingredient, excluded: str) -> None:
    assert is_eligible(recipe([ingredient]), ask(must_exclude=[excluded]))


# --- several constraints ----------------------------------------------------------


def compound() -> Extraction:
    return ask(diet="vegan", max_total_minutes=30, must_include=["potato"], must_exclude=["nut"])


def test_all_constraints_pass_together() -> None:
    r = recipe(
        [ing("potatoes")], minutes=explicit(25), vegetarian=derived(True), vegan=derived(True)
    )
    assert is_eligible(r, compound())


def test_every_failing_constraint_is_reported_none_is_dropped() -> None:
    r = recipe(
        [ing("cashews")], minutes=explicit(40), vegetarian=derived(True), vegan=derived(False)
    )
    assert why_ineligible(r, compound()) == [
        "total time 40 min, need < 30 min",
        "not vegan (False, derived)",
        "no ingredient matches 'potato'",
        "contains 'cashews', excluded by 'nut'",
    ]


@pytest.mark.parametrize(
    "broken",
    [
        {"max_total_minutes": 20},
        {"vegan": derived(False)},
        {"ingredients": [ing("carrots")]},
        {"ingredients": [ing("potatoes"), ing("pine nuts")]},
    ],
)
def test_one_failing_constraint_makes_the_recipe_ineligible(broken: dict[str, Any]) -> None:
    fields: dict[str, Any] = {
        "ingredients": [ing("potatoes")],
        "minutes": explicit(25),
        "vegetarian": derived(True),
        "vegan": derived(True),
    }
    if "max_total_minutes" in broken:
        fields["minutes"] = explicit(35)
    else:
        fields.update(broken)
    assert not is_eligible(recipe(**fields), compound())


def test_no_constraints_make_every_recipe_eligible() -> None:
    assert all(is_eligible(r, ask()) for r in CORPUS.values())


def test_the_unsatisfiable_golden_combination_has_no_recipe() -> None:
    constraints = ask(diet="vegan", max_total_minutes=30, must_include=["potato"])
    assert [rid for rid, r in CORPUS.items() if is_eligible(r, constraints)] == []


def test_describe_names_every_constraint() -> None:
    assert describe(compound()) == ["vegan", "under 30 minutes", "with potato", "without nut"]
    assert describe(ask(max_total_minutes=20, time_limit_inclusive=True)) == ["20 minutes or less"]
    assert describe(ask()) == []


# --- the alias table --------------------------------------------------------------


def test_alias_table_matches_its_spec_scope() -> None:
    table = json.loads((ROOT / "data" / "aliases.json").read_text(encoding="utf-8"))
    assert set(table) == {"description", "aliases", "exclude_only"}
    assert {"nut", "dairy", "egg"} <= set(table["aliases"])
    assert set(table["exclude_only"]) == {"gluten"}
    assert "gluten" not in table["aliases"]
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    assert re.search(r"Alias table scope \(`data/aliases.json`", spec)
    for key in table["aliases"]:
        assert key == key.lower() and len(ALIASES.include(key)) > 1, key
