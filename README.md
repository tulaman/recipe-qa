# Recipe Q&A

A small service that answers natural-language questions about recipes from
a fixed corpus of Wikibooks Cookbook pages, with citations and
machine-readable refusals.

- Specification: [SPEC.md](SPEC.md) (written before the code)
- Pipeline diagram (question to answer, where the LLM runs):
  [docs/pipeline.md](docs/pipeline.md)
- Decisions: [adr/](adr/)
- Eval harness: [evals/](evals/)
- AI usage notes: [docs/ai-notes.md](docs/ai-notes.md), [CLAUDE.md](CLAUDE.md)

Status: in progress. Sections marked _TBD_ are filled as the corresponding
stage lands; nothing here claims to be done before it is.

## Public URLs

- UI: https://recipe-qa-production.up.railway.app/
- API: https://recipe-qa-production.up.railway.app (`POST /ask`,
  `GET /healthz`, `GET /schema`)

```bash
curl -s https://recipe-qa-production.up.railway.app/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "How do I make spaghetti alla carbonara?"}'
```
- Container-level access for reviewers: Railway project **Viewer**
  invitation (read-only deployments, logs, container status). _TBD: invite
  sent on receipt of the reviewers' e-mail address._

## Run locally

```bash
# with Docker (preferred)
git rev-parse HEAD > recipe_qa/BUILD_SHA   # optional: /healthz reports "dev" without it
docker build -t recipe-qa .
docker run --rm -p 8000:8000 -e ANTHROPIC_API_KEY=... recipe-qa
# open http://localhost:8000

# without Docker
uv sync
(cd web && npm ci && npm run build)          # UI served from web/dist
uv run uvicorn recipe_qa.api:app --reload    # needs ANTHROPIC_API_KEY
```

No key? `RECIPE_QA_PROVIDER=fake` runs the whole pipeline with a
deterministic stand-in model (`recipe_qa/providers/fake.py`): retrieval,
gates, citations and the contract are real, the answer text is not.
`(cd web && npm run dev)` serves the UI with hot reload and proxies the API
to `localhost:8000`.

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
uv run python evals/run.py --base-url http://localhost:8000                  # smoke set, 3 runs per case
uv run python evals/run.py --base-url http://localhost:8000 --contract-only  # fake provider
uv run python evals/extract.py --set smoke                                   # extraction only, needs a key
```

Evaluation is layered (SPEC §7, details in [evals/README.md](evals/README.md)):
`evals/extract.py` measures constraint extraction on its own labelled set,
field by field; `evals/run.py` sends a case set to a running instance and
checks every response against the contract (schema, invariants I1–I5,
citations resolve to the manifest, `/healthz`, `/schema`, UI served) and
each case's expected refusal, sources and constraint predicates (checked
against corpus metadata). Each case runs three times; a case that passes
only sometimes, or changes outcome, is reported as unstable, not as
passing. False answers, false refusals and wrong refusal reasons are
counted separately. Every report records the build, prompt version, model,
corpus and case set it ran against. Exploratory reports go to
`evals/reports/` (ignored); `--milestone NAME` writes a report to commit
under `evals/reports/milestones/`. A held-out paraphrase set, kept apart
from every other set, runs once, as a milestone.

Sets: `smoke` (three questions: a how-to answer, `out_of_domain`, and a
`safety` refusal with citations). The 15-case golden set and the held-out
paraphrases land in stage 5.

_TBD: last eval report summary._

## Deployment

One Docker image (API + built UI, non-root user) on Railway; decisions in
[ADR 003](adr/003-deployment.md).

| Piece | Where |
|---|---|
| Image | `Dockerfile` (multi-stage: Vite build → Python 3.12 slim, runtime deps only) |
| Infrastructure (IaC) | `.railway/railway.ts` (Railway IaC, TypeScript): the whole project — service, Dockerfile builder, `/healthz` health check, restart policy, 1 replica, variables |
| CI | `.github/workflows/ci.yml`: ruff + pytest; UI type-check + build; `railway.ts` type-check and, on pull requests, the production plan in the job summary; image build, boot with the fake provider, contract smoke against the container |
| CD | `.github/workflows/deploy.yml`: runs after CI succeeds on `main` |

Deploy flow: `railway config apply` converges the project to
`railway.ts` → `railway config plan --detailed-exit-code` fails the deploy
if anything is still pending → set `ANTHROPIC_API_KEY` from GitHub secrets
without triggering a deploy → write the commit to `recipe_qa/BUILD_SHA` →
`railway up` builds the commit → wait until `/healthz` reports that exact
SHA → smoke eval against the public URL with the live model; the report is
kept as a workflow artifact.

```bash
cd .railway && npm ci && cd ..
railway config plan   # what the next deploy would change (needs a linked project)
```

- **Idempotent**: apply converges to the file and the plan check proves
  it; re-running the workflow for the same SHA yields the same service.
- **Destructive changes**: a deploy never deletes a Railway resource or
  variable on its own; it fails at apply. Read the plan, then Actions →
  deploy → Run workflow with `allow_destructive`.
- **Rollback**: Actions → deploy → Run workflow → enter an older commit
  SHA. The workflow checks it out and deploys it the same way, applying
  that commit's `railway.ts`; `/healthz` confirms which build is live.
  Commits before the IaC migration (no `.railway/railway.ts`) are refused.
- **Secrets**: `ANTHROPIC_API_KEY` and a project-scoped `RAILWAY_TOKEN`
  live only in GitHub Actions secrets; the key is copied into Railway's
  service variables by the workflow. Nothing secret is in the repository
  or the image.
- Deploys never run from a laptop; Railway's own GitHub auto-deploy is off,
  so there is exactly one path to production.

## Cost & latency

_TBD: measured token usage, cost per question and per 1,000, latency
percentiles, model choice and switching conditions, current bottleneck and
next optimization._

## Finding the cause of a bad answer in production

Every response, including errors, carries a request ID (`X-Request-ID`
header, `meta.request_id` / `error.request_id` in the body, shown in the
UI). Each `/ask` request writes one JSON log line (Railway → service →
Logs, search the ID) with: the question, build SHA, model, prompt version
(hash of `recipe_qa/prompts/`), corpus version, the extracted constraints
and flags, eligible count, BM25 top-5 with scores, the IDs sent to the
model, the IDs it cited, refusal reason or error, invalid-output retries,
token usage per call and timings per stage. That is enough to tell which
layer failed: extraction (wrong flags), retrieval (right recipe absent
from top-k), or generation (right recipe given, wrong answer), and to
replay the request against the same prompt and corpus versions. Questions
are recipe questions, not personal data, and are logged in full (SPEC
§9.7); Railway keeps logs for its plan's retention window, and we would set
30 days in a production deployment.

## Scope cut and production gaps

_TBD (filled at the end)._ Recorded so far:

- Stage 3 extracts constraints but does not yet enforce them: `is_eligible`
  over the whole corpus lands in stage 4. Until then a time/diet/ingredient
  constraint is only as good as BM25 ranking and the model.
- Rate limiting and the concurrency cap are in process memory, keyed on
  Railway's `X-Real-IP`; correct for one instance only. A second replica
  needs a shared store (Redis) or the edge's own limiter.
- Railway's generated domain cannot be declared in `.railway/railway.ts`;
  it is the one piece of infrastructure outside the file. One environment only (production); no staging.

- Ingredient alias table (`data/aliases.json`) lands with constraint
  matching in stage 4, not with ingestion.
- Ingredient `normalized` names are heuristic; exclusion matching must also
  read `raw` (ADR 004).
- Pages whose diet category contradicts their ingredients are excluded,
  not repaired; the eligibility rule also ignores such a category
  (SPEC §4 v0.2).
