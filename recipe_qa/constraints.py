"""Constraint application (SPEC §4): code decides which recipes are eligible.

`why_ineligible` checks every constraint and returns every failure, so a
trace shows that no constraint was dropped; `is_eligible` is its negation.
Both read only ingested metadata (time and diet facts already carry the
§4 rules, ranges at their upper bound) and the ingredient list.

Ingredient matching is a whole-word, case- and accent-insensitive match of
the term or one of its aliases (`data/aliases.json`), singular or plural
(including -y/-ies and -f/-ves). Inclusion
looks at the normalized name (§4); exclusion also at the raw line, which
keeps parenthetical detail ("any nuts, e.g. pistachios"), and counts
optional ingredients. A dairy or egg word with a plant qualifier before it
("coconut milk") or a non-animal noun after it ("butter beans") is not a
match, by the same rule as the diet check; nor is an allergen the line
itself negates ("non-dairy", "egg-free mayonnaise").
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from pathlib import Path

from recipe_qa.corpus import Ingredient, Recipe
from recipe_qa.ingestion.diet import ANIMAL_PRODUCTS, NOT_ANIMAL_AFTER, PLANT_BEFORE
from recipe_qa.retrieval import fold, tokenize
from recipe_qa.schemas import Extraction

ALIASES_PATH = Path(__file__).resolve().parent.parent / "data" / "aliases.json"
"""The committed table. The service loads the one next to its configured
recipes instead (api.create_app), so a data directory is one snapshot."""
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


@functools.cache
def default_aliases() -> AliasTable:
    """The committed table, read on first use rather than at import."""
    return AliasTable.load()


def _pattern(term: str) -> re.Pattern[str]:
    """`term` as whole words, accent-folded, its last word singular or plural.

    Group 1 is the part every form shares, so callers can look right after it.
    """
    words = fold(term).split()
    last = (tokenize(words[-1]) or [words[-1]])[0] if words else ""
    stem = re.escape(" ".join([*words[:-1], last]))
    if last.endswith("y"):  # berry, berries; turkey, turkeys
        return re.compile(rf"\b({stem[:-1]})(?:y|ys|ies)\b")
    if last.endswith("fe"):  # knife, knives
        return re.compile(rf"\b({stem[:-2]})(?:fe|fes|ves)\b")
    if last.endswith("f"):  # leaf, leaves; loaf, loaves
        return re.compile(rf"\b({stem[:-1]})(?:f|fs|ves)\b")
    return re.compile(rf"\b({stem})(?:s|es)?\b")


_FREE_AFTER = re.compile(r"^[\s-]*free\b")
_DAIRY_NEGATIONS = frozenset({"non-dairy", "dairy-free"})
_NOT_DAIRY = frozenset(
    {"egg", "yolk", "albumen", "mayonnaise", "mayo", "meringue", "custard", "honey"}
)
"""Animal words a "dairy-free" qualifier says nothing about: dairy-free mayonnaise has egg."""


def _negated_before(item: str, term: str) -> re.Pattern[str]:
    """ "non-dairy", "dairy-free cheese", "egg free mayo": the line says the
    asked-for allergen is absent. Only the constraint's own item or the
    matched term negate; "sugar-free milk" is still milk."""
    words = "|".join(re.escape(fold(w)) for w in (item, term))
    return re.compile(
        rf"(?:\bnon[\s-]*|\bnon[\s-]*(?:{words})[\s-]+"
        rf"|\b(?:{words})(?:s|es)?[\s-]+free[\s-]+)$"
    )


def _matches(text: str, item: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        negated = _negated_before(item, term)
        for match in _pattern(term).finditer(text):
            before = text[: match.start()]
            if negated.search(before) or _FREE_AFTER.match(text[match.end() :]):
                continue
            if match[1] in _ANIMAL_WORDS:
                after = text[match.start() + len(match[1]) :]
                plant = PLANT_BEFORE.search(before)
                if plant and plant[1] in _DAIRY_NEGATIONS and match[1] in _NOT_DAIRY:
                    plant = None
                if plant or NOT_ANIMAL_AFTER.match(after):
                    continue
            return True
    return False


def _included(ingredient: Ingredient, item: str, terms: tuple[str, ...]) -> bool:
    return _matches(fold(ingredient.normalized), item, terms)


def _excluded(ingredient: Ingredient, item: str, terms: tuple[str, ...]) -> bool:
    return _matches(fold(f"{ingredient.raw} | {ingredient.normalized}"), item, terms)


def why_ineligible(
    recipe: Recipe, constraints: Extraction, aliases: AliasTable | None = None
) -> list[str]:
    """Every constraint `recipe` fails; empty when it is eligible."""
    aliases = aliases or default_aliases()
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
        if not any(_included(i, item, terms) for i in recipe.ingredients):
            failures.append(f"no ingredient matches {item!r}")

    for item in constraints.must_exclude:
        terms = aliases.exclude(item)
        for ingredient in recipe.ingredients:
            if _excluded(ingredient, item, terms):
                optional = " (optional)" if ingredient.optional else ""
                failures.append(f"contains {ingredient.raw!r}{optional}, excluded by {item!r}")

    return failures


def is_eligible(recipe: Recipe, constraints: Extraction, aliases: AliasTable | None = None) -> bool:
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
