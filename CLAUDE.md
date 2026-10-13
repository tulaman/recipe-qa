# Agent instructions for this repository

Read SPEC.md first. It is the contract; code follows it, not the reverse.
If an implementation detail conflicts with SPEC.md, stop and raise it —
never silently change behavior.

## Language

Code, comments, commit messages and all documents: English.

## Working rules

- Spec and tests before code. For `is_eligible`, time parsing, ingredient
  normalization, ingestion parsing and the API contract, commit the failing
  test first, then the implementation.
- Small commits, one concern each. Commit messages explain *why*.
- No provider key is needed for `pytest`; tests use the fake provider in
  `recipe_qa/providers/fake.py`. Live calls happen only in `evals/`.
- Never put secrets in the repo. `ANTHROPIC_API_KEY` and `RAILWAY_TOKEN`
  come from the environment.
- Do not add dependencies without a one-line justification in the commit.
- Do not add features outside SPEC.md scope; record cut scope in README.

## Layout

```
recipe_qa/      application package (api, schemas, constraints, retrieval,
                generation, service, providers/)
scripts/        corpus selection and ingestion
data/           committed corpus snapshot (jsonl, manifest, raw wikitext)
tests/          deterministic tests
evals/          golden set + HTTP harness (live provider)
web/            Vite + TypeScript one-page UI
adr/            architecture decision records
docs/           ai-notes.md, prompts
```

## Decisions already made (do not reopen without an ADR update)

- Whole-recipe retrieval, BM25 in memory, no vector DB (ADR 001).
- LLM extracts constraints; code applies them; model cites only given IDs
  (ADR 002).
- `safety` refusals keep citations (SPEC §5).
- One container, Railway, deployed from GitHub Actions (ADR 003).
