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

```bash
make corpus          # scripts/select_corpus.py → scripts/ingest.py → data/
```

_TBD: category list, counts, how pinned revisions work, how to refresh._

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

_TBD._
