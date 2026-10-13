"""Selection entry + pinned wikitext → Recipe record; records → Manifest."""

from recipe_qa.corpus import Manifest, Recipe, _Frozen


class SelectionEntry(_Frozen):
    title: str
    page_id: int
    revid: int
    fetched_at: str
    wikitext_sha256: str
    reasons: list[str]


def recipe_id(title: str) -> str:
    raise NotImplementedError


def wiki_url(title: str) -> str:
    raise NotImplementedError


def build_record(entry: SelectionEntry, wikitext: str) -> Recipe:
    raise NotImplementedError


def build_manifest(recipes: list[Recipe]) -> Manifest:
    raise NotImplementedError
