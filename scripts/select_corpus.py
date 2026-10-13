"""Select 40–60 Wikibooks Cookbook recipes and pin their revisions.

Writes data/corpus_selection.json and data/raw/<id>.wiki. Run deliberately
(`make select-corpus`): it reads live Wikibooks, so its output changes when
pages change. `scripts/ingest.py` then rebuilds data/ from the pinned
selection alone.

Selection is deterministic for a given state of Wikibooks:

1. Anchors: a fixed list of pages the golden set needs (overlapping dishes,
   nut questions, a paraphrase target). Each must pass the quality filter.
2. Quotas: for each variety group, pages from its category are ordered by
   sha256(SEED + title) — a reproducible pseudo-random sample, not "the
   oldest" or "the first alphabetically" — and taken alternately from
   timed and untimed pages, so both time-constraint cases and messy pages
   are represented. Pages already selected count toward every group they
   belong to. Groups then take turns adding one page each up to
   TARGET_SIZE, which keeps the corpus balanced across groups.
3. Quality filter: a Cookbook recipe page (not a disambiguation page),
   ≥ 3 ingredients and ≥ 2 steps after parsing, no {{incomplete recipe}},
   recipe text ≤ MAX_RECIPE_CHARS, and no diet category contradicted by
   its own ingredient list.
"""

import hashlib
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import wikibooks_api as wb

from recipe_qa.ingestion.diet import category_conflicts
from recipe_qa.ingestion.page import ParsedPage, parse_page
from recipe_qa.ingestion.records import (
    MAX_RECIPE_CHARS,
    SOURCE,
    Selection,
    SelectionEntry,
    context_chars,
    raw_path,
    recipe_id,
    wiki_url,
)
from recipe_qa.ingestion.timeparse import parse_total_minutes

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SEED = "recipe-qa/2026-10-13"
MIN_SIZE, MAX_SIZE = 40, 60
TARGET_SIZE = 50

# Group → (categories that define membership, quota).
GROUPS: dict[str, tuple[tuple[str, ...], int]] = {
    "soup": (("Soup recipes", "Recipes for soup"), 6),
    "dessert": (("Dessert recipes", "Recipes for dessert"), 6),
    "italian": (("Italian recipes",), 6),
    "indian": (("Indian recipes",), 6),
    "mexican": (("Mexican recipes",), 6),
    "vegetarian": (("Vegetarian recipes",), 6),
    "vegan": (("Vegan recipes",), 5),
    "gluten_free": (("Gluten-free recipes",), 5),
}

ANCHORS: dict[str, str] = {
    "Cookbook:Spaghetti alla Carbonara": "anchor: paraphrase target (creamy pasta, cured pork)",
    "Cookbook:Carbonara Pasta": "anchor: second carbonara (overlap / conflicting recipe)",
    "Cookbook:Rice Pudding": "anchor: rice pudding variant (no time, optional items)",
    "Cookbook:Kheer (Rice Pudding)": "anchor: rice pudding variant (Indian)",
    "Cookbook:Italian Rice Pudding": "anchor: rice pudding variant (Italian)",
    "Cookbook:Spicy Tomato Soup": "anchor: tomato soup variant",
    "Cookbook:Mexican Tomato Soup": "anchor: tomato soup variant",
    "Cookbook:Baklava I": "anchor: nut question target (walnuts; messy sections)",
    "Cookbook:Baklava II": "anchor: second baklava (any nuts; overlap)",
    "Cookbook:Guacamole I": "anchor: guacamole variant",
    "Cookbook:Chunky Guacamole": "anchor: guacamole variant",
}
OVERLAP_GROUPS: dict[str, list[str]] = {
    "carbonara": ["Cookbook:Spaghetti alla Carbonara", "Cookbook:Carbonara Pasta"],
    "rice pudding": [
        "Cookbook:Rice Pudding",
        "Cookbook:Kheer (Rice Pudding)",
        "Cookbook:Italian Rice Pudding",
    ],
    "tomato soup": ["Cookbook:Spicy Tomato Soup", "Cookbook:Mexican Tomato Soup"],
    "baklava": ["Cookbook:Baklava I", "Cookbook:Baklava II"],
    "guacamole": ["Cookbook:Guacamole I", "Cookbook:Chunky Guacamole"],
}

RULES = [
    "Anchors (golden-set targets) are always included and must pass the quality filter.",
    "Per variety group, candidates are ordered by sha256(seed + title) and taken "
    "alternately from timed and untimed pages until the group quota is met; then groups "
    f"take turns adding one page each until the corpus has {TARGET_SIZE} pages.",
    "Quality filter: Cookbook recipe page, not a disambiguation page, >= 3 ingredients, "
    f">= 2 steps, no {{{{incomplete recipe}}}}, recipe text <= {MAX_RECIPE_CHARS} chars, "
    "no diet category contradicted by its ingredient list.",
    f"Seed: {SEED}. Quotas: " + ", ".join(f"{g}={q}" for g, (_, q) in GROUPS.items()) + ".",
]


def rejection(rev: wb.Revision, page: ParsedPage) -> str | None:
    lowered = rev.wikitext.lower()
    if not rev.title.startswith("Cookbook:"):
        return "not a Cookbook page"
    if "{{cookdp" in lowered or "Cookbook disambiguation pages" in page.categories:
        return "disambiguation page"
    if "{{incomplete recipe" in lowered:
        return "marked incomplete"
    if len(page.ingredients) < 3:
        return f"{len(page.ingredients)} ingredients"
    if len(page.steps) < 2:
        return f"{len(page.steps)} steps"
    size = context_chars(rev.title, page.ingredients, page.steps, page.sections)
    if size > MAX_RECIPE_CHARS:
        return f"{size} chars of recipe text"
    if conflicts := category_conflicts(page.categories, page.ingredients):
        return "; ".join(conflicts)
    return None


def order_key(title: str) -> str:
    return hashlib.sha256(f"{SEED}:{title}".encode()).hexdigest()


def groups_of(page: ParsedPage) -> set[str]:
    return {g for g, (cats, _) in GROUPS.items() if set(cats) & set(page.categories)}


def main() -> int:
    pool: set[str] = set(ANCHORS)
    for categories, _ in GROUPS.values():
        for category in categories:
            pool.update(wb.category_members(f"Category:{category}"))
    print(f"candidate pool: {len(pool)} pages")

    revisions = wb.latest_revisions(sorted(pool))
    parsed = {title: parse_page(rev.wikitext) for title, rev in revisions.items()}
    rejected = {t: r for t in parsed if (r := rejection(revisions[t], parsed[t]))}
    eligible = sorted((t for t in parsed if t not in rejected), key=order_key)
    print(f"eligible: {len(eligible)}, rejected: {len(rejected)}")

    reasons: dict[str, list[str]] = {}
    for title, reason in ANCHORS.items():
        if title not in revisions:
            sys.exit(f"anchor {title!r} not found on Wikibooks")
        if title in rejected:
            sys.exit(f"anchor {title!r} rejected: {rejected[title]}")
        reasons[title] = [reason]

    timed_by_group: dict[str, list[str]] = {}
    untimed_by_group: dict[str, list[str]] = {}
    for group in GROUPS:
        members = [t for t in eligible if group in groups_of(parsed[t])]
        timed_by_group[group] = [
            t for t in members if parse_total_minutes(parsed[t].summary_time).value
        ]
        untimed_by_group[group] = [t for t in members if t not in timed_by_group[group]]
    turns = Counter[str]()

    def take(group: str, why: str) -> bool:
        """Add the next unselected page of `group`, alternating timed/untimed."""
        queues = [timed_by_group[group], untimed_by_group[group]]
        if turns[group] % 2:
            queues.reverse()
        for queue in queues:
            while queue:
                title = queue.pop(0)
                if title not in reasons:
                    reasons[title] = [why]
                    turns[group] += 1
                    return True
        return False

    def count(group: str) -> int:
        return sum(1 for t in reasons if group in groups_of(parsed[t]))

    for group, (_, quota) in GROUPS.items():
        while count(group) < quota and take(group, f"quota: {group}"):
            pass
    while len(reasons) < TARGET_SIZE:
        progressed = False
        for group in GROUPS:
            if len(reasons) < TARGET_SIZE and take(group, f"top-up: {group}"):
                progressed = True
        if not progressed:
            break

    selected = sorted(reasons, key=recipe_id)
    if not MIN_SIZE <= len(selected) <= MAX_SIZE:
        sys.exit(f"selected {len(selected)} pages, outside {MIN_SIZE}-{MAX_SIZE}")
    ids = [recipe_id(t) for t in selected]
    if len(set(ids)) != len(ids):
        sys.exit(f"duplicate ids: {[i for i, n in Counter(ids).items() if n > 1]}")

    canonical = wb.canonical_urls(selected)
    mismatched = {t: (canonical[t], wiki_url(t)) for t in selected if canonical[t] != wiki_url(t)}
    if mismatched:
        sys.exit(f"wiki_url() disagrees with the API canonical URL: {mismatched}")

    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    raw_dir = DATA / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for stale in set(raw_dir.glob("*.wiki")) - {raw_path(raw_dir, t) for t in selected}:
        stale.unlink()
    entries = []
    for title in selected:
        rev = revisions[title]
        raw_path(raw_dir, title).write_text(rev.wikitext, encoding="utf-8")
        entries.append(
            SelectionEntry(
                title=title,
                page_id=rev.page_id,
                revid=rev.revid,
                fetched_at=fetched_at,
                wikitext_sha256=hashlib.sha256(rev.wikitext.encode()).hexdigest(),
                reasons=reasons[title],
            )
        )
    selection = Selection(
        generated_at=fetched_at,
        source=SOURCE,
        rules=RULES,
        overlap_groups={
            dish: [recipe_id(t) for t in titles] for dish, titles in OVERLAP_GROUPS.items()
        },
        recipes=entries,
    )
    (DATA / "corpus_selection.json").write_text(
        json.dumps(selection.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    group_counts = Counter(g for t in selected for g in groups_of(parsed[t]))
    print(f"selected {len(selected)} pages; per group: {dict(sorted(group_counts.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
