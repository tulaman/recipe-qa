"""Selection entry + pinned wikitext → Recipe record; records → Manifest."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote

from recipe_qa.corpus import (
    Fact,
    FrozenModel,
    Ingredient,
    Manifest,
    ManifestEntry,
    Recipe,
    corpus_version,
)
from recipe_qa.corpus import record_hash as compute_record_hash
from recipe_qa.ingestion.diet import derive_diet
from recipe_qa.ingestion.page import parse_page
from recipe_qa.ingestion.timeparse import parse_total_minutes

WIKI_BASE = "https://en.wikibooks.org/wiki/"
SOURCE = "English Wikibooks Cookbook (https://en.wikibooks.org), CC BY-SA 4.0"
NAMESPACE = "Cookbook:"
# Characters MediaWiki leaves unescaped in canonical URLs (wfUrlencode).
_URL_SAFE = ";@$!*(),/~:"
# ≈ 2k tokens of recipe text; above this, whole-recipe retrieval stops
# fitting the top-3 context budget (ADR 001 invalidation condition).
MAX_RECIPE_CHARS = 8000


class SelectionEntry(FrozenModel):
    title: str
    page_id: int
    revid: int
    fetched_at: str
    wikitext_sha256: str
    reasons: list[str]


class Selection(FrozenModel):
    """data/corpus_selection.json: what to ingest, pinned to revisions."""

    generated_at: str
    source: str
    rules: list[str]
    overlap_groups: dict[str, list[str]]
    """Dish → recipe IDs that describe the same dish (golden cases 4 and 5)."""
    recipes: list[SelectionEntry]


def recipe_id(title: str) -> str:
    name = title.removeprefix(NAMESPACE)
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


def wiki_url(title: str) -> str:
    return WIKI_BASE + quote(title.replace(" ", "_"), safe=_URL_SAFE)


def context_chars(
    title: str, ingredients: list[Ingredient], steps: list[str], sections: dict[str, str]
) -> int:
    """Characters of recipe text a generation prompt would carry."""
    parts = [title, *(i.raw for i in ingredients), *steps, *sections.values()]
    return sum(len(p) for p in parts)


def _servings(raw: str | None) -> Fact[str]:
    if raw is None:
        return Fact.unknown()
    return Fact(value=raw, status="explicit", evidence=raw)


def _difficulty(raw: str | None) -> Fact[int]:
    if raw is None:
        return Fact.unknown()
    if re.fullmatch(r"[1-5]", raw.strip()):
        return Fact(value=int(raw), status="explicit", evidence=raw)
    return Fact.unknown(evidence=raw)


def build_record(entry: SelectionEntry, wikitext: str) -> Recipe:
    actual = hashlib.sha256(wikitext.encode()).hexdigest()
    if actual != entry.wikitext_sha256:
        raise ValueError(
            f"{entry.title}: wikitext sha256 {actual} does not match pinned "
            f"{entry.wikitext_sha256} (revid {entry.revid})"
        )
    page = parse_page(wikitext)
    record = {
        "id": recipe_id(entry.title),
        "title": entry.title.removeprefix(NAMESPACE),
        "url": wiki_url(entry.title),
        "page_id": entry.page_id,
        "revid": entry.revid,
        "fetched_at": entry.fetched_at,
        "content_hash": "",
        "categories": page.categories,
        "ingredients": [i.model_dump(mode="json") for i in page.ingredients],
        "steps": page.steps,
        "sections": page.sections,
        "metadata": {
            "total_minutes": parse_total_minutes(page.summary_time).model_dump(mode="json"),
            "servings": _servings(page.summary_servings).model_dump(mode="json"),
            "difficulty": _difficulty(page.summary_difficulty).model_dump(mode="json"),
            "diet": derive_diet(page.categories, page.ingredients).model_dump(mode="json"),
        },
    }
    # Validate first so the hash is computed over the normalized, typed record.
    recipe = Recipe.model_validate(record)
    dumped = recipe.model_dump(mode="json")
    return recipe.model_copy(update={"content_hash": compute_record_hash(dumped)})


def build_manifest(recipes: list[Recipe]) -> Manifest:
    ids = [r.id for r in recipes]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate recipe ids: {duplicates}")
    ordered = sorted(recipes, key=lambda r: r.id)
    return Manifest(
        corpus_version=corpus_version((r.id, r.content_hash) for r in ordered),
        source=SOURCE,
        recipe_count=len(ordered),
        recipes=[
            ManifestEntry(
                id=r.id, title=r.title, url=r.url, revid=r.revid, content_hash=r.content_hash
            )
            for r in ordered
        ],
    )


def load_selection(path: Path) -> Selection:
    return Selection.model_validate_json(path.read_text(encoding="utf-8"))


def raw_path(raw_dir: Path, title: str) -> Path:
    return raw_dir / f"{recipe_id(title)}.wiki"


def build_corpus(selection: Selection, raw_dir: Path) -> tuple[list[Recipe], Manifest]:
    """Offline rebuild from pinned raw wikitext. Raises if a raw page is missing."""
    recipes = [
        build_record(entry, raw_path(raw_dir, entry.title).read_text(encoding="utf-8"))
        for entry in selection.recipes
    ]
    recipes.sort(key=lambda r: r.id)
    return recipes, build_manifest(recipes)


def dump_recipes(recipes: list[Recipe]) -> str:
    return "".join(r.model_dump_json() + "\n" for r in recipes)


def dump_manifest(manifest: Manifest) -> str:
    return json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
