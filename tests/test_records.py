"""Selection entry + pinned wikitext → Recipe record and manifest (SPEC §6)."""

import hashlib
from pathlib import Path

import pytest

from recipe_qa.corpus import corpus_version, record_hash
from recipe_qa.ingestion.records import (
    SelectionEntry,
    build_manifest,
    build_record,
    recipe_id,
    wiki_url,
)

FIXTURES = Path(__file__).parent / "fixtures"
CARBONARA = (FIXTURES / "carbonara.wiki").read_text(encoding="utf-8")


def entry_for(wikitext: str, title: str = "Cookbook:Spaghetti alla Carbonara") -> SelectionEntry:
    return SelectionEntry(
        title=title,
        page_id=22971,
        revid=4640105,
        fetched_at="2026-10-13T00:00:00Z",
        wikitext_sha256=hashlib.sha256(wikitext.encode()).hexdigest(),
        reasons=["anchor: paraphrase case"],
    )


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Cookbook:Spaghetti alla Carbonara", "spaghetti-alla-carbonara"),
        ("Cookbook:Kheer (Rice Pudding)", "kheer-rice-pudding"),
        ("Cookbook:Eärgon Salsa", "eargon-salsa"),
        ("Cookbook:Lentil, Barley, and Tomato Soup", "lentil-barley-and-tomato-soup"),
    ],
)
def test_recipe_id_is_a_readable_slug(title: str, expected: str) -> None:
    assert recipe_id(title) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Cookbook:Spaghetti alla Carbonara",
            "https://en.wikibooks.org/wiki/Cookbook:Spaghetti_alla_Carbonara",
        ),
        (
            "Cookbook:Kheer (Rice Pudding)",
            "https://en.wikibooks.org/wiki/Cookbook:Kheer_(Rice_Pudding)",
        ),
        ("Cookbook:Eärgon Salsa", "https://en.wikibooks.org/wiki/Cookbook:E%C3%A4rgon_Salsa"),
    ],
)
def test_wiki_url_is_canonical(title: str, expected: str) -> None:
    assert wiki_url(title) == expected


def test_build_record_fills_identity_and_metadata() -> None:
    recipe = build_record(entry_for(CARBONARA), CARBONARA)
    assert recipe.id == "spaghetti-alla-carbonara"
    assert recipe.title == "Spaghetti alla Carbonara"
    assert recipe.url == "https://en.wikibooks.org/wiki/Cookbook:Spaghetti_alla_Carbonara"
    assert (recipe.page_id, recipe.revid) == (22971, 4640105)
    meta = recipe.metadata
    assert (meta.total_minutes.value, meta.total_minutes.status) == (60, "explicit")
    assert (meta.servings.value, meta.servings.status) == ("6", "explicit")
    assert (meta.difficulty.value, meta.difficulty.status) == (2, "explicit")
    assert (meta.diet.vegetarian.value, meta.diet.vegetarian.status) == (False, "derived")
    assert meta.diet.gluten_free.status == "unknown"


def test_content_hash_covers_the_parsed_record() -> None:
    recipe = build_record(entry_for(CARBONARA), CARBONARA)
    assert recipe.content_hash == record_hash(recipe.model_dump(mode="json"))


def test_build_is_deterministic() -> None:
    first = build_record(entry_for(CARBONARA), CARBONARA)
    second = build_record(entry_for(CARBONARA), CARBONARA)
    assert first.model_dump_json() == second.model_dump_json()


def test_wikitext_not_matching_the_pinned_hash_is_rejected() -> None:
    with pytest.raises(ValueError, match="sha256"):
        build_record(entry_for(CARBONARA), CARBONARA + "\nedited")


def test_unparseable_difficulty_is_unknown_with_evidence() -> None:
    text = CARBONARA.replace("difficulty=2", "difficulty=easy")
    recipe = build_record(entry_for(text), text)
    assert recipe.metadata.difficulty.status == "unknown"
    assert recipe.metadata.difficulty.evidence == "easy"


def test_manifest_version_is_order_independent_and_content_sensitive() -> None:
    carbonara = build_record(entry_for(CARBONARA), CARBONARA)
    other_text = CARBONARA.replace("servings=6", "servings=4")
    other = build_record(
        entry_for(other_text, "Cookbook:Carbonara Variant").model_copy(update={"page_id": 1}),
        other_text,
    )
    manifest = build_manifest([carbonara, other])
    assert manifest.recipe_count == 2
    assert manifest.corpus_version == build_manifest([other, carbonara]).corpus_version
    assert manifest.corpus_version == corpus_version(
        [(carbonara.id, carbonara.content_hash), (other.id, other.content_hash)]
    )
    assert [e.id for e in manifest.recipes] == sorted([carbonara.id, other.id])
    assert manifest.corpus_version != build_manifest([carbonara]).corpus_version


def test_manifest_rejects_duplicate_ids() -> None:
    carbonara = build_record(entry_for(CARBONARA), CARBONARA)
    with pytest.raises(ValueError, match="duplicate"):
        build_manifest([carbonara, carbonara])


@pytest.mark.parametrize(
    "replacement",
    ["yield=about 12 oz", "servings=|yield=about 12 oz", "servings= |yield=about 12 oz"],
)
def test_yield_fills_servings_when_servings_is_absent(replacement: str) -> None:
    text = CARBONARA.replace("servings=6", replacement)
    servings = build_record(entry_for(text), text).metadata.servings
    assert (servings.value, servings.status) == ("about 12 oz", "explicit")
    assert servings.evidence == "yield: about 12 oz"


def test_servings_wins_over_yield() -> None:
    text = CARBONARA.replace("servings=6", "servings=6|yield=1 pot")
    servings = build_record(entry_for(text), text).metadata.servings
    assert (servings.value, servings.evidence) == ("6", "6")
