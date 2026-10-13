# Recipe Q&A

A small service that answers natural-language questions about recipes from
a fixed corpus of Wikibooks Cookbook pages, with citations and
machine-readable refusals.

- Specification: [SPEC.md](SPEC.md) (written before the code)
- Decisions: [adr/](adr/)
- Eval harness: [evals/](evals/)
- AI usage notes: [docs/ai-notes.md](docs/ai-notes.md), [CLAUDE.md](CLAUDE.md)

Status: in progress. Sections marked _TBD_ are filled as the corresponding
stage lands; nothing here claims to be done before it is.

## Public URLs

- UI: _TBD_
- API: _TBD_ (`POST /ask`, `GET /healthz`, `GET /schema`)
- Container-level access for reviewers: Railway project **Viewer**
  invitation (read-only deployments, logs, container status). _TBD: invite
  sent on receipt of the reviewers' e-mail address._

## Run locally

```bash
# with Docker (preferred)
docker build -t recipe-qa .
docker run --rm -p 8000:8000 -e ANTHROPIC_API_KEY=... recipe-qa
# open http://localhost:8000

# without Docker
uv sync
uv run uvicorn recipe_qa.api:app --reload
(cd web && npm ci && npm run build)
```

## Corpus

50 English Wikibooks Cookbook recipes (CC BY-SA 4.0), committed under
`data/`. Decisions and measurements: [ADR 004](adr/004-ingestion.md).

| File | What |
|---|---|
| `data/corpus_selection.json` | Which pages, why (anchor / group quota), pinned `revid` and SHA-256 of the wikitext, overlap groups |
| `data/raw/<id>.wiki` | Pinned raw wikitext, for audits and the offline rebuild test |
| `data/recipes.jsonl` | Parsed records: ingredients, steps, sections, metadata with provenance (`explicit` / `derived` / `unknown` + evidence) |
| `data/manifest.json` | IDs, titles, canonical URLs, per-record hashes, `corpus_version` |

Coverage: soup 9, dessert 10, Italian 8, Indian 8, Mexican 8, vegetarian 13,
vegan 13, gluten-free 7 (pages count in every group they belong to).
Overlapping dishes: carbonara ×2, rice pudding ×3, tomato soup ×2,
baklava ×2, guacamole ×2. Time: 27 explicit, 3 derived (summed stages),
20 unknown.

```bash
make corpus          # rebuild recipes.jsonl + manifest.json from the pinned selection (offline)
uv run python scripts/ingest.py --check   # exit 1 if committed data drifted from the parser
make select-corpus   # deliberate refresh from live Wikibooks: new revids, new corpus_version
```

`make corpus` is reproducible: a missing raw page is fetched by its pinned
`revid` and rejected unless its hash matches. `tests/test_corpus.py` fails
if the committed files differ from a rebuild, so a parser change cannot
ship without regenerating the data. A refresh is a reviewed commit, never
a runtime behavior (SPEC §9.5).

## Tests and evals

```bash
uv run pytest                      # deterministic tests, no network, no key
uv run python evals/run.py --base-url http://localhost:8000
```

_TBD: last eval report summary._

## Deployment

_TBD: Railway IaC file, `deploy.yml` flow, idempotency, rollback, secrets._

## Cost & latency

_TBD: measured token usage, cost per question and per 1,000, latency
percentiles, model choice and switching conditions, current bottleneck and
next optimization._

## Finding the cause of a bad answer in production

_TBD: one paragraph on request IDs, structured logs and what is recorded
per stage._

## Scope cut and production gaps

_TBD (filled at the end)._ Recorded so far:

- Ingredient alias table (`data/aliases.json`) lands with constraint
  matching in stage 4, not with ingestion.
- Ingredient `normalized` names are heuristic; exclusion matching must also
  read `raw` (ADR 004).
- Pages whose diet category contradicts their ingredients are excluded,
  not repaired.
