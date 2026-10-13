"""Prompts and the text the model sees (SPEC §3 steps 3 and 7).

The prompts are versioned files; PROMPT_VERSION is a hash of their
content and is logged with every request, so an answer can be traced to
the exact instructions that produced it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from recipe_qa.corpus import Fact, Recipe

PROMPTS = Path(__file__).resolve().parent / "prompts"
EXTRACT_SYSTEM = (PROMPTS / "extract.md").read_text(encoding="utf-8")
GENERATE_SYSTEM = (PROMPTS / "generate.md").read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256((EXTRACT_SYSTEM + GENERATE_SYSTEM).encode()).hexdigest()[:12]

# Never shown to the model, so it cannot answer from them (SPEC §9.9).
HIDDEN_SECTIONS = frozenset({"nutrition"})


def _fact(fact: Fact[object], unit: str = "") -> str:
    if fact.status == "unknown":
        return "unknown"
    value = {True: "yes", False: "no"}.get(fact.value, fact.value)  # type: ignore[arg-type]
    text = f"{value}{unit} ({fact.status}"
    if fact.status == "derived" and fact.evidence:
        text += f": {fact.evidence}"
    return text + ")"


def render_recipe(recipe: Recipe) -> str:
    meta = recipe.metadata
    lines = [
        f'<recipe id="{recipe.id}">',
        f"Title: {recipe.title}",
        f"Total time: {_fact(meta.total_minutes, ' minutes')}",
        f"Servings: {_fact(meta.servings)}",
        f"Vegetarian: {_fact(meta.diet.vegetarian)}",
        f"Vegan: {_fact(meta.diet.vegan)}",
        f"Gluten-free: {_fact(meta.diet.gluten_free)}",
        "Ingredients:",
    ]
    group: str | None = None
    for ingredient in recipe.ingredients:
        if ingredient.group != group:
            group = ingredient.group
            lines.append(f"  {group}:" if group else "  (main):")
        optional = " (optional)" if ingredient.optional else ""
        lines.append(f"  - {ingredient.raw}{optional}")
    lines.append("Steps:")
    lines.extend(f"  {n}. {step}" for n, step in enumerate(recipe.steps, start=1))
    for name, text in recipe.sections.items():
        if name not in HIDDEN_SECTIONS:
            lines.append(f"{name.capitalize()}: {text}")
    lines.append("</recipe>")
    return "\n".join(lines)


def extract_message(question: str) -> str:
    return f"<question>{question}</question>"


def generate_message(question: str, recipes: Sequence[Recipe]) -> str:
    blocks = "\n\n".join(render_recipe(r) for r in recipes)
    return f"{blocks}\n\n<question>{question}</question>"
