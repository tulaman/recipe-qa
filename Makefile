.PHONY: corpus select-corpus test lint

# Rebuild data/recipes.jsonl and data/manifest.json from the pinned selection.
# Offline when data/raw/ is complete; a missing raw page is fetched by its
# pinned revid and checked against the recorded hash.
corpus:
	uv run python scripts/ingest.py

# Deliberate refresh: re-run the selection against live Wikibooks and pin
# new revision IDs. Changes corpus_version; review the diff before committing.
select-corpus:
	uv run python scripts/select_corpus.py
	uv run python scripts/ingest.py

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .
