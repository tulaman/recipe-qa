"""Prompts and the text the model sees (SPEC §3 steps 3 and 7).

The prompts are versioned files; PROMPT_VERSION is a hash of their
content and is logged with every request, so an answer can be traced to
the exact instructions that produced it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from recipe_qa.constraints import describe
from recipe_qa.corpus import Fact, Recipe
from recipe_qa.schemas import Extraction

PROMPTS = Path(__file__).resolve().parent / "prompts"
EXTRACT_SYSTEM = (PROMPTS / "extract.md").read_text(encoding="utf-8")
GENERATE_SYSTEM = (PROMPTS / "generate.md").read_text(encoding="utf-8")
PROMPT_VERSION = hashlib.sha256((EXTRACT_SYSTEM + GENERATE_SYSTEM).encode()).hexdigest()[:12]

# Never shown to the model, so it cannot answer from them (SPEC §9.9).
HIDDEN_SECTIONS = frozenset({"nutrition"})


def _data(text: str) -> str:
    """Neutralize angle brackets so data can never open or close a prompt tag.

    Only < and > are replaced: escaping & as well would leak "&amp;" into
    answers about "salt & pepper" and adds nothing, since no tag can be
    formed without brackets.
    """
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _fact(fact: Fact[object], unit: str = "") -> str:
    if fact.status == "unknown":
        return "unknown"
    value = {True: "yes", False: "no"}.get(fact.value, fact.value)  # type: ignore[arg-type]
    text = f"{_data(str(value))}{unit} ({fact.status}"
    if fact.status == "derived" and fact.evidence:
        text += f": {_data(fact.evidence)}"
    return text + ")"


def render_recipe(recipe: Recipe) -> str:
    meta = recipe.metadata
    lines = [
        f'<recipe id="{recipe.id}">',
        f"Title: {_data(recipe.title)}",
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
            lines.append(f"  {_data(group)}:" if group else "  (main):")
        optional = " (optional)" if ingredient.optional else ""
        lines.append(f"  - {_data(ingredient.raw)}{optional}")
    lines.append("Steps:")
    lines.extend(f"  {n}. {_data(step)}" for n, step in enumerate(recipe.steps, start=1))
    for name, text in recipe.sections.items():
        if name not in HIDDEN_SECTIONS:
            lines.append(f"{_data(name.capitalize())}: {_data(text)}")
    lines.append("</recipe>")
    return "\n".join(lines)


def extract_message(question: str) -> str:
    return f"<question>{_data(question)}</question>"


def render_constraints(constraints: Extraction) -> str:
    """What code already applied to every recipe shown (SPEC §3 step 7)."""
    lines = [f"- {_data(part)}" for part in describe(constraints)] or ["none"]
    return "<constraints>\n" + "\n".join(lines) + "\n</constraints>"


def generate_message(question: str, constraints: Extraction, recipes: Sequence[Recipe]) -> str:
    blocks = "\n\n".join(render_recipe(r) for r in recipes)
    return (
        f"{render_constraints(constraints)}\n\n{blocks}\n\n<question>{_data(question)}</question>"
    )
