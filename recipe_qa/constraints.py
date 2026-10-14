"""Constraint application (SPEC §4): code decides which recipes are eligible.

`why_ineligible` checks every constraint and returns every failure, so a
trace shows that no constraint was dropped; `is_eligible` is its negation.
Both read only ingested metadata (time and diet facts already carry the
§4 rules, ranges at their upper bound) and the ingredient list.

Ingredient matching is a whole-word, case-insensitive match of the term or
one of its aliases (`data/aliases.json`), singular or plural. Inclusion
looks at the normalized name (§4); exclusion also at the raw line, which
keeps parenthetical detail ("any nuts, e.g. pistachios"), and counts
optional ingredients. A dairy or egg word with a plant qualifier before it
("coconut milk") or a non-animal noun after it ("butter beans") is not a
match, by the same rule as the diet check.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from recipe_qa.corpus import Ingredient, Recipe
from recipe_qa.ingestion.diet import ANIMAL_PRODUCTS, NOT_ANIMAL_AFTER, PLANT_BEFORE
from recipe_qa.retrieval import tokenize
from recipe_qa.schemas import Extraction

ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "aliases.json"
_ANIMAL_WORDS = frozenset(ANIMAL_PRODUCTS)


def _key(term: str) -> str:
    return " ".join(tokenize(term))


@dataclass(frozen=True)
class AliasTable:
    aliases: dict[str, tuple[str, ...]]
    exclude_only: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: Path = ALIASES_PATH) -> AliasTable:
        table = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            aliases={_key(k): tuple(v) for k, v in table["aliases"].items()},
            exclude_only={_key(k): tuple(v) for k, v in table["exclude_only"].items()},
        )

    def include(self, term: str) -> tuple[str, ...]:
        """The term and the aliases a must_include on it also accepts."""
        return (term, *self.aliases.get(_key(term), ()))

    def exclude(self, term: str) -> tuple[str, ...]:
        """The term and everything a must_exclude on it also rejects."""
        return (*self.include(term), *self.exclude_only.get(_key(term), ()))


ALIASES = AliasTable.load()


def _pattern(term: str) -> re.Pattern[str]:
    """`term` as whole words, its last word singular or plural."""
    words = term.lower().split()
    last = (tokenize(words[-1]) or [words[-1]])[0] if words else ""
    stem = re.escape(" ".join([*words[:-1], last]))
    if last.endswith("y"):
        return re.compile(rf"\b({stem[:-1]})(?:y|ies)\b")
    return re.compile(rf"\b({stem})(?:s|es)?\b")


def _matches(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        for match in _pattern(term).finditer(text):
            if match[1] in _ANIMAL_WORDS:
                before, after = text[: match.start()], text[match.start() + len(match[1]) :]
                if PLANT_BEFORE.search(before) or NOT_ANIMAL_AFTER.match(after):
                    continue
            return True
    return False


def _included(ingredient: Ingredient, terms: tuple[str, ...]) -> bool:
    return _matches(ingredient.normalized.lower(), terms)


def _excluded(ingredient: Ingredient, terms: tuple[str, ...]) -> bool:
    return _matches(f"{ingredient.raw} | {ingredient.normalized}".lower(), terms)


def why_ineligible(
    recipe: Recipe, constraints: Extraction, aliases: AliasTable = ALIASES
) -> list[str]:
    """Every constraint `recipe` fails; empty when it is eligible."""
    failures = []
    meta = recipe.metadata

    limit = constraints.max_total_minutes
    if limit is not None:
        inclusive = constraints.time_limit_inclusive
        minutes = meta.total_minutes.value
        need = f"need {'≤' if inclusive else '<'} {limit} min"
        if minutes is None:
            # We never assume a recipe is fast.
            failures.append(f"total time unknown, {need}")
        elif not (minutes <= limit if inclusive else minutes < limit):
            failures.append(f"total time {minutes} min, {need}")

    diet = constraints.diet
    if diet is not None:
        fact = getattr(meta.diet, diet)
        # Gluten-free is never derived: only an explicit category vouches.
        if fact.value is not True or (diet == "gluten_free" and fact.status != "explicit"):
            failures.append(f"not {diet} ({fact.value}, {fact.status})")

    for item in constraints.must_include:
        terms = aliases.include(item)
        if not any(_included(i, terms) for i in recipe.ingredients):
            failures.append(f"no ingredient matches {item!r}")

    for item in constraints.must_exclude:
        terms = aliases.exclude(item)
        for ingredient in recipe.ingredients:
            if _excluded(ingredient, terms):
                optional = " (optional)" if ingredient.optional else ""
                failures.append(f"contains {ingredient.raw!r}{optional}, excluded by {item!r}")

    return failures


def is_eligible(recipe: Recipe, constraints: Extraction, aliases: AliasTable = ALIASES) -> bool:
    return not why_ineligible(recipe, constraints, aliases)


def describe(constraints: Extraction) -> list[str]:
    """The constraints in words, for an out_of_corpus message that names them."""
    parts = []
    if constraints.diet is not None:
        parts.append(constraints.diet.replace("_", "-"))
    if constraints.max_total_minutes is not None:
        n = constraints.max_total_minutes
        parts.append(
            f"{n} minutes or less" if constraints.time_limit_inclusive else f"under {n} minutes"
        )
    parts += [f"with {item}" for item in constraints.must_include]
    parts += [f"without {item}" for item in constraints.must_exclude]
    return parts
