"""What the model is shown: one recipe block per candidate (SPEC §3, §4, §9)."""

from pathlib import Path

from recipe_qa.corpus import load_recipes
from recipe_qa.generation import generate_message, render_recipe

DATA = Path(__file__).resolve().parent.parent / "data"
RECIPES = {r.id: r for r in load_recipes(DATA / "recipes.jsonl")}


def test_recipe_block_carries_id_ingredients_steps_and_provenance() -> None:
    recipe = RECIPES["agnolotti-pasta-soup-agnolotti-in-brodo"]
    text = render_recipe(recipe)

    assert text.startswith('<recipe id="agnolotti-pasta-soup-agnolotti-in-brodo">')
    assert "Total time: 15 minutes (explicit)" in text
    assert "Vegetarian: no (derived: ingredient" in text
    for ingredient in recipe.ingredients:
        assert ingredient.raw in text
    assert f"5. {recipe.steps[4]}" in text


def test_nutrition_sections_are_never_shown() -> None:
    with_nutrition = [r for r in RECIPES.values() if "nutrition" in r.sections]
    assert with_nutrition, "corpus has a page with a nutrition section"
    for recipe in with_nutrition:
        assert recipe.sections["nutrition"] not in render_recipe(recipe)


def test_unknown_time_is_rendered_as_unknown() -> None:
    recipe = next(r for r in RECIPES.values() if r.metadata.total_minutes.status == "unknown")
    assert "Total time: unknown" in render_recipe(recipe)


def test_message_puts_the_question_after_the_recipes() -> None:
    message = generate_message("How long?", [RECIPES["baklava-i"], RECIPES["baklava-ii"]])
    assert message.index('id="baklava-i"') < message.index('id="baklava-ii"')
    assert message.endswith("<question>How long?</question>")
