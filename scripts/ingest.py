"""Rebuild data/recipes.jsonl and data/manifest.json from the pinned selection.

Offline when data/raw/ is complete. A missing raw page is fetched by its
pinned revid; build_record() then checks it against the recorded sha256,
so the output is the same on every machine or the run fails.

    uv run python scripts/ingest.py          # rebuild and write
    uv run python scripts/ingest.py --check  # exit 1 if committed data drifted
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

from recipe_qa.ingestion.records import (
    build_corpus,
    dump_manifest,
    dump_recipes,
    load_selection,
    raw_path,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def fetch_missing_raw(raw_dir: Path) -> None:
    selection = load_selection(DATA / "corpus_selection.json")
    missing = [e for e in selection.recipes if not raw_path(raw_dir, e.title).exists()]
    if not missing:
        return
    import wikibooks_api as wb  # network only when needed

    print(f"fetching {len(missing)} pinned revisions")
    revisions = wb.revisions_by_id([e.revid for e in missing])
    raw_dir.mkdir(parents=True, exist_ok=True)
    for entry in missing:
        if entry.revid not in revisions:
            sys.exit(f"revid {entry.revid} ({entry.title}) not available")
        raw_path(raw_dir, entry.title).write_text(revisions[entry.revid].wikitext, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="compare instead of writing")
    args = parser.parse_args()

    raw_dir = DATA / "raw"
    if not args.check:
        fetch_missing_raw(raw_dir)
    recipes, manifest = build_corpus(load_selection(DATA / "corpus_selection.json"), raw_dir)
    outputs = {
        DATA / "recipes.jsonl": dump_recipes(recipes),
        DATA / "manifest.json": dump_manifest(manifest),
    }

    if args.check:
        drifted = [p.name for p, text in outputs.items() if p.read_text(encoding="utf-8") != text]
        if drifted:
            print(f"drift in {drifted}; run `make corpus`")
            return 1
        print(f"ok: {manifest.recipe_count} recipes, corpus_version {manifest.corpus_version}")
        return 0

    for path, text in outputs.items():
        path.write_text(text, encoding="utf-8")

    def tally(field: str) -> dict[str, int]:
        return dict(Counter(getattr(r.metadata, field).status for r in recipes))

    print(f"{manifest.recipe_count} recipes, corpus_version {manifest.corpus_version}")
    print(f"total_minutes: {tally('total_minutes')}")
    for diet in ("vegetarian", "vegan", "gluten_free"):
        statuses = Counter(
            (getattr(r.metadata.diet, diet).status, getattr(r.metadata.diet, diet).value)
            for r in recipes
        )
        print(f"{diet}: {dict(statuses)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
