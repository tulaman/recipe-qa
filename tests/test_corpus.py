"""The committed corpus snapshot (SPEC §6).

Runs offline against data/. Checks shape, identity, variety and that the
committed files are exactly what the parser produces from data/raw/ — so a
parser change without `make corpus` fails CI instead of drifting silently.
"""

from collections import Counter
from functools import cache
from pathlib import Path

import pytest

from recipe_qa.corpus import Recipe, corpus_version, load_manifest, load_recipes, record_hash
from recipe_qa.ingestion.diet import category_conflicts
from recipe_qa.ingestion.records import (
    MAX_RECIPE_CHARS,
    WIKI_BASE,
    build_corpus,
    context_chars,
    dump_manifest,
    dump_recipes,
    load_selection,
)

DATA = Path(__file__).resolve().parents[1] / "data"

VARIETY_GROUPS = {
    "soup": {"Soup recipes", "Recipes for soup"},
    "dessert": {"Dessert recipes", "Recipes for dessert"},
    "italian": {"Italian recipes"},
    "indian": {"Indian recipes"},
    "mexican": {"Mexican recipes"},
    "vegetarian_or_vegan": {"Vegetarian recipes", "Vegan recipes"},
    "gluten_free": {"Gluten-free recipes"},
}


@cache
def recipes() -> list[Recipe]:
    return load_recipes(DATA / "recipes.jsonl")


def test_size_is_within_spec() -> None:
    assert 40 <= len(recipes()) <= 60


def test_identity_fields_are_unique() -> None:
    for field in ("id", "url", "title", "page_id"):
        values = [getattr(r, field) for r in recipes()]
        assert len(values) == len(set(values)), field


def test_only_cookbook_recipe_pages() -> None:
    for recipe in recipes():
        assert recipe.url.startswith(WIKI_BASE + "Cookbook:"), recipe.id
        assert "Cookbook disambiguation pages" not in recipe.categories, recipe.id
        assert not recipe.title.startswith("Category:"), recipe.id


def test_every_recipe_has_ingredients_and_steps() -> None:
    for recipe in recipes():
        assert len(recipe.ingredients) >= 3, recipe.id
        assert len(recipe.steps) >= 2, recipe.id


def test_every_recipe_fits_the_context_budget() -> None:
    for recipe in recipes():
        size = context_chars(recipe.title, list(recipe.ingredients), recipe.steps, recipe.sections)
        assert size <= MAX_RECIPE_CHARS, recipe.id


def test_hashes_and_manifest_are_consistent() -> None:
    manifest = load_manifest(DATA / "manifest.json")
    for recipe in recipes():
        assert recipe.content_hash == record_hash(recipe.model_dump(mode="json")), recipe.id
    assert manifest.recipe_count == len(recipes())
    assert {e.id: e.url for e in manifest.recipes} == {r.id: r.url for r in recipes()}
    assert manifest.corpus_version == corpus_version((r.id, r.content_hash) for r in recipes())


def test_committed_files_match_a_rebuild_from_raw() -> None:
    selection = load_selection(DATA / "corpus_selection.json")
    rebuilt, manifest = build_corpus(selection, DATA / "raw")
    assert (DATA / "recipes.jsonl").read_text(encoding="utf-8") == dump_recipes(rebuilt)
    assert (DATA / "manifest.json").read_text(encoding="utf-8") == dump_manifest(manifest)


def test_variety_categories() -> None:
    counts = {
        group: sum(1 for r in recipes() if names & set(r.categories))
        for group, names in VARIETY_GROUPS.items()
    }
    covered = [group for group, n in counts.items() if n >= 3]
    assert len(covered) >= 5, counts


def test_overlapping_dishes() -> None:
    selection = load_selection(DATA / "corpus_selection.json")
    ids = {r.id for r in recipes()}
    complete = [dish for dish, members in selection.overlap_groups.items() if set(members) <= ids]
    assert len(complete) == len(selection.overlap_groups)
    assert len(complete) >= 2
    assert all(len(selection.overlap_groups[d]) >= 2 for d in complete)


def test_enough_explicit_times_for_time_constraints() -> None:
    explicit = [r for r in recipes() if r.metadata.total_minutes.status == "explicit"]
    assert len(explicit) >= 15


def test_enough_explicit_diet_categories() -> None:
    diet = [
        r
        for r in recipes()
        if any(
            f.status == "explicit"
            for f in (
                r.metadata.diet.vegetarian,
                r.metadata.diet.vegan,
                r.metadata.diet.gluten_free,
            )
        )
    ]
    assert len(diet) >= 10


def test_enough_messy_pages() -> None:
    grouped = [r for r in recipes() if any(i.group for i in r.ingredients)]
    untimed = [r for r in recipes() if r.metadata.total_minutes.status == "unknown"]
    assert len(grouped) >= 5
    assert len(untimed) >= 5


def test_no_diet_category_contradicted_by_ingredients() -> None:
    for recipe in recipes():
        assert category_conflicts(recipe.categories, list(recipe.ingredients)) == [], recipe.id


@pytest.mark.parametrize("status", ["explicit", "derived", "unknown"])
def test_provenance_statuses_all_occur(status: str) -> None:
    # Guards against a parser regression that collapses everything to one status.
    statuses = Counter(r.metadata.total_minutes.status for r in recipes())
    statuses.update(r.metadata.diet.vegetarian.status for r in recipes())
    assert statuses[status] > 0
