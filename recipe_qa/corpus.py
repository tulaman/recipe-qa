"""Recipe record and manifest schema (SPEC §6).

The same models are written by ingestion and read by the service, so a
record that does not validate here never reaches retrieval.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

Status = Literal["explicit", "derived", "unknown"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Fact[T](FrozenModel):
    """A metadata value together with where it came from.

    explicit: stated on the page; derived: computed by a documented rule,
    with the evidence kept; unknown: neither, and the value is None.
    """

    value: T | None
    status: Status
    evidence: str | None = None

    @model_validator(mode="after")
    def _value_matches_status(self) -> Self:
        if (self.status == "unknown") != (self.value is None):
            raise ValueError(f"status={self.status!r} is inconsistent with value={self.value!r}")
        return self

    @classmethod
    def unknown(cls, evidence: str | None = None) -> Self:
        return cls(value=None, status="unknown", evidence=evidence)


class Ingredient(FrozenModel):
    raw: str
    """Display text of the ingredient line, wiki markup removed."""
    normalized: str
    """Lower-case ingredient name without quantity, unit or preparation note."""
    optional: bool
    group: str | None = None
    """Sub-heading the line appears under ("Syrup", "Filling"), if any."""


class Diet(FrozenModel):
    vegetarian: Fact[bool]
    vegan: Fact[bool]
    gluten_free: Fact[bool]


class Metadata(FrozenModel):
    total_minutes: Fact[int]
    servings: Fact[str]
    difficulty: Fact[int]
    diet: Diet


class Recipe(FrozenModel):
    id: str
    title: str
    url: str
    page_id: int
    revid: int
    fetched_at: str
    content_hash: str
    categories: list[str]
    ingredients: list[Ingredient]
    steps: list[str]
    sections: dict[str, str]
    metadata: Metadata


class ManifestEntry(FrozenModel):
    id: str
    title: str
    url: str
    revid: int
    content_hash: str


class Manifest(FrozenModel):
    corpus_version: str
    source: str
    recipe_count: int
    recipes: list[ManifestEntry]


def canonical_json(data: object) -> str:
    """Stable serialization used for hashing and for the committed files."""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_hash(record: dict[str, object]) -> str:
    """SHA-256 of a record's canonical JSON, excluding the hash field itself.

    Hashing the parsed record (not only the wikitext) means a parser change
    that alters what the service sees also changes corpus_version.
    """
    body = {k: v for k, v in record.items() if k != "content_hash"}
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def corpus_version(entries: Iterable[tuple[str, str]]) -> str:
    """SHA-256 over sorted (id, content_hash) pairs (SPEC §6)."""
    digest = hashlib.sha256()
    for recipe_id, content_hash in sorted(entries):
        digest.update(f"{recipe_id}\t{content_hash}\n".encode())
    return digest.hexdigest()


def load_recipes(path: Path) -> list[Recipe]:
    with path.open(encoding="utf-8") as fh:
        return [Recipe.model_validate_json(line) for line in fh if line.strip()]


def load_manifest(path: Path) -> Manifest:
    return Manifest.model_validate_json(path.read_text(encoding="utf-8"))
