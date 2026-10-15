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

Status: complete for the assessment (stage 6, 2026-10-15). Every number
below cites the committed report it comes from.

## Public URLs

- UI: https://recipe-qa-production.up.railway.app/
- API: https://recipe-qa-production.up.railway.app (`POST /ask`,
  `GET /healthz`, `GET /schema`)

```bash
curl -s https://recipe-qa-production.up.railway.app/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "How do I make spaghetti alla carbonara?"}'
```
- Container-level access for reviewers: Railway project **Viewer** role
  (read-only deployments, build and runtime logs, container status and
  metrics; no variables). Procedure: send the e-mail address that should
  have access; a Viewer invitation to the `recipe-qa` project is sent on
  receipt, and the link in Railway's e-mail opens the project.

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
uv run pytest                                            # deterministic tests, no network, no key
uv run pytest tests/test_retrieval_recall.py -s          # retrieval recall@5 on the golden set
uv run python evals/run.py --base-url http://localhost:8000                  # smoke set, 3 runs per case
uv run python evals/run.py --base-url http://localhost:8000 --set golden     # golden set, 3 runs per case
uv run python evals/run.py --base-url http://localhost:8000 --contract-only  # fake provider
uv run python evals/extract.py --set golden                                  # extraction only, needs a key
uv run python evals/rescore.py evals/reports/milestones/*-golden*.json       # recount old reports, offline
uv run python evals/stages.py --report REPORT.json --log server.log          # latency/tokens per stage
```

`stages.py` joins a run's report with the server's trace log (start the
server with its output tee'd to `evals/reports/server.log`).

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
`safety` refusal with citations), `golden` (15 cases, one per scenario in
SPEC §7.2), the extraction set (32 labelled questions) and `holdout` (5
paraphrases, run once in stage 6).

Final results, `claude-haiku-5-5`, corpus `976abf38…`, prompts
`d259a00c2700`; reports in
[`evals/reports/milestones/`](evals/reports/milestones/):

| Measure | Result | Report |
|---|---|---|
| Golden on the public URL, hard checks | 15/15 cases in every run (3 runs, build `5d8af05`) | [`stage-6-public-golden.json`](evals/reports/milestones/stage-6-public-golden.json) |
| Golden on the public URL, content checks | 15/15, 15/15, 15/15 (need ≥ 13 per run); no unstable case | same |
| Refusals over 45 public runs | false answers 0, false refusals 0, wrong reason 0, errors 0 | same |
| Golden, local (stage 5b, build `ad4a74a`) | hard 15/15 and content 15/15 in every run; same refusal counts | [`stage-5b-golden.json`](evals/reports/milestones/stage-5b-golden.json) |
| **Held-out set, run once** (public URL, build `5d8af05`) | **5/5 cases pass**, content 5/5; false answers 0, false refusals 0 | [`stage-6-public-holdout.json`](evals/reports/milestones/stage-6-public-holdout.json) |
| Extraction | 30/32 cases every run; unstable: g02, x17 (spurious `must_include`) | [`stage-5b-extraction.json`](evals/reports/milestones/stage-5b-extraction.json) |
| Retrieval recall@5 | 11/11 answerable golden cases | `tests/test_retrieval_recall.py` |
| Factuality review (33 public answers read against their pages) | added steps 0, invented time or quantity 0, merged recipes 0; 4 wording notes | [`evals/factuality_review.md`](evals/factuality_review.md) |
| Latency | see [Cost & latency](#cost--latency) | — |

The held-out number is reported apart from golden: it is five
paraphrases written before tuning, never used to change a prompt, case or
rule, and run exactly once (the harness refuses a second run). Five cases
is a small sample; it says the golden result is not specific to the
golden wording, not more. How the golden numbers moved from the stage 4b
baseline, and why, is in [docs/ai-notes.md](docs/ai-notes.md) and in
ADR 001 / 002.

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

Every number here comes from a committed report; medians and p95 are
nearest-rank. Two measured setups, same prompts (`d259a00c2700`), model
and corpus:

| Setup | Build | Served from | Client reports | Server stages |
|---|---|---|---|---|
| **Public** | `5d8af05` | Railway, `us-west2`, one instance, warm; client on the author's laptop over the internet | [`stage-6-public-golden.json`](evals/reports/milestones/stage-6-public-golden.json) | [`stage-6-public-stages.json`](evals/reports/milestones/stage-6-public-stages.json) |
| **Local** | `ad4a74a` | `127.0.0.1`, clean export of the commit | [`stage-5b-golden.json`](evals/reports/milestones/stage-5b-golden.json) | [`stage-5b-stages.json`](evals/reports/milestones/stage-5b-stages.json) |

Each is golden × 3 = 45 requests: 33 answers, 9 refusals at a
deterministic gate (g11 no eligible recipe, g13 `out_of_domain`, g14
`safety`), 3 refusals by the model (g12, absent fact). The two builds
differ only outside the prompt files (the "nut free" guard and how total
time is rendered to the model). Server stages come from each request's
trace, joined by request ID (45/45 matched in both); for the public run
the traces were read from Railway's log (`railway logs --json`).

### Tokens per request

Median (p95), summed over attempts; no request had an invalid-output retry.

| Stage | Answers, public | Answers, local | Refusals, public | Refusals, local |
|---|---|---|---|---|
| extract, in | 1,641 (1,650) | 1,641 (1,650) | 1,641 (1,653) | 1,641 (1,653) |
| extract, out | 65 (75) | 65 (74) | 19 (73) | 19 (73) |
| generate, in | 3,554 (4,542) | 3,554 (4,543) | 3,624 (3,624), g12 only | 3,602 (3,602), g12 only |
| generate, out | 314 (480) | 252 (550) | 25 (25), g12 only | 25 (25), g12 only |

Gate refusals make one call (extract); only the model's own refusal
(g12) reaches generate. Extraction input is mostly the output schema,
which is why it is 1.6k tokens, not the ~400 ADR 002 estimated.

### Cost

Formula, Claude Haiku 5.5 list prices on 2026-10-13 (SPEC §8):
`cost = input_tokens × $0.10 / 1M + output_tokens × $0.50 / 1M`,
with the public medians above:

| Request | Tokens in / out | Per question | Per 1,000 |
|---|---|---|---|
| Answer (median) | 1,641 + 3,554 = 5,195 / 65 + 314 = 379 | $0.00071 | **$0.71** |
| Answer (p95 tokens in every stage) | 6,192 / 555 | $0.00090 | $0.90 |
| Gate refusal (extract only) | 1,641 / 19 | $0.00017 | $0.17 |
| Model refusal (g12) | 5,265 / 44 | $0.00055 | $0.55 |
| Golden mix (33 : 9 : 3) | — | $0.00059 | $0.59 |

Target ≤ $1.00 per 1,000 questions: met, also at p95 tokens. The SPEC §8
projection of ≈ $0.55 is missed for answered questions ($0.71): the
extraction input is 3× the estimate (above). The projection is not
rewritten.

### Latency

Milliseconds, median / p95. Client = what the eval harness measured
(network included); server = the trace.

| | Public | Local |
|---|---|---|
| **Answers** (33) — extract | 997 / 1,634 | 1,193 / 1,953 |
| retrieve (filter + BM25) | 0.2 / 9.4 | 0.4 / 14.8 |
| generate | 1,950 / 2,957 | 1,996 / 3,105 |
| server total | 3,099 / 4,247 | 3,282 / 4,695 |
| **client, end to end** | **3,346 / 4,581** | 3,284 / 4,698 |
| **Gate refusals** (9), client | **1,158 / 1,298** | 1,064 / 1,241 |
| Model refusal g12 (3), client | 3,279 / 3,850 | 2,167 / 2,461 |
| All 12 refusals, server total | 968 / 3,627 | 1,124 / 2,459 |

Client-side numbers by outcome are computed from the per-run
`client_latency_ms` in the golden reports. The server stages are
dominated by the provider, so public and local differ by the provider's
run-to-run variation more than by the host; the network adds ≈ 250 ms
at the median (client 3,346 vs server 3,099).

SPEC §8 targets:

| Target | Measured | Met |
|---|---|---|
| Answered, warm, p95 ≤ 5 s, ≥ 30 requests | 4.58 s public (33 requests; one above 5 s: g08, 5,084 ms) | yes |
| Refusal via deterministic gate, p95 ≤ 2 s | 1.30 s public | yes, on 9 requests: the golden set has 3 gate cases, so this sample is below 30 |
| Hard deadline 12 s, then 503 | slowest request 5.1 s; the deadline itself is covered by `tests/test_api.py`, `tests/test_service.py` | not reached |
| Cost ≤ $1.00 / 1,000 | $0.71 answered, $0.59 golden mix | yes |
| Cold start ≤ 10 s | 4.1 s on Railway ([ADR 003](adr/003-deployment.md)) | yes |
| Daily provider spend cap documented | a **monthly** $30 cap exists; no daily cap, by decision (below) | no |

### Bottleneck and next optimization

`generate` is the bottleneck: 63 % of the median server time for an
answer (1,950 of 3,099 ms) and its p95 is 70 % of the server p95 (2,957
of 4,247 ms); `extract` is the rest, retrieval is under 10 ms. Generation
time follows output length (314 tokens median, 480 at p95). The next
optimization would be **streaming the answer text** of the generate call
to the UI: perceived latency would drop from the full generate time to
extract plus the first tokens, at no change in cost. It is not free:
citations are validated only once the output is complete, so the UI
must be able to replace a streamed answer with a refusal or error when
validation fails. Cut from scope for that reason (below).

### Model choice and when to switch

Claude Haiku 5.5 for both calls ([ADR 002](adr/002-generation-and-refusal.md)):
the hard parts (eligibility, citation validation, refusal gates) are in
code, and on Haiku the golden set passes every hard and content check in
every run, publicly and locally, with 0 false answers. Measurable
conditions for switching:

- **To Sonnet 5.5 for `generate`**: content checks below 13/15 in any
  golden run after prompt fixes, or the factuality review finding any
  added step, invented time or quantity, or merged recipe. Expected cost
  ≈ $8–10 per 1,000 (ADR 002), so it needs that evidence first.
- **To Sonnet 5.5 for `extract`**: more than 1 of 15 golden questions
  mis-parsed after prompt tuning (stage 5b: 1, g02, unstable). The cheaper
  first step is the rule-based pre-pass ADR 002 keeps as a fallback.
- **To something cheaper**: Haiku is already the cheapest current Claude
  model, so cheaper means taking calls away. Extraction is 32 % of an
  answer's input tokens and all of a gate refusal's cost; a rule-based
  extractor that keeps extraction at ≥ 30/32 and golden at 15/15 would
  cut cost per answer by about a quarter ($0.71 → $0.51 per 1,000). Trigger: cost above $1.00 per
  1,000, or the monthly cap reached before month end at real traffic.

### Provider spend cap

- **What**: $30 per calendar month for the whole Anthropic workspace that
  holds the production key; every key in that workspace counts against
  it. There is no daily cap, by decision (2026-10-15): the monthly cap at
  the provider is the spend control for this deployment, and no
  in-process budget is added. SPEC §8 asks for a daily cap; that target is
  recorded as not met, not rewritten. $30 is about 42,000 answered
  questions a month (≈ 1,400 a day) at $0.71 per 1,000.
- **Where**: Anthropic Console → Billing → Spend limits, set by the
  account owner. It is not in the repository or in `railway.ts`; there is
  nothing to declare it with.
- **What happens when reached**: the API answers HTTP 400
  `invalid_request_error` ("You have reached your specified workspace API
  usage limits", Anthropic docs, Rate limits → Spend limits, read
  2026-10-15) until the next month or until the limit is raised. Every
  `/ask` then answers **503 `upstream_unavailable`** (SPEC §2.3 v0.7);
  `/healthz`, `/schema` and the UI keep working. Stage 6 found that this
  response would have become 500 `internal_error`; fixed in stage 6b
  (`recipe_qa/providers/claude.py`, `tests/test_claude_provider.py`,
  tested with the documented error shape, not reproduced live).
- **Why the rate limit is not a spend limit**: one client at 30 requests a
  minute for 24 hours sends 43,200 requests, ≈ $31 at the answered
  price, so a single address can spend the month's cap in about a day.

## Finding the cause of a bad answer in production

Every response, including errors, carries a request ID (`X-Request-ID`
header, `meta.request_id` / `error.request_id` in the body, shown in the
UI). Each `/ask` request writes one JSON log line (Railway → service →
Logs, search the ID; level `info`, `warn` on a 4xx, `error` on a 5xx)
with: the question, build SHA, model, prompt version
(hash of `recipe_qa/prompts/`), corpus version, the extracted constraints
and flags, eligible count and why each other recipe was rejected, BM25 top-5 with scores, the IDs sent to the
model, the IDs it cited, refusal reason or error, invalid-output retries,
token usage per call and timings per stage. That is enough to tell which
layer failed: extraction (wrong flags), retrieval (right recipe absent
from top-k), or generation (right recipe given, wrong answer), and to
replay the request against the same prompt and corpus versions. Questions
are recipe questions, not personal data, and are logged in full (SPEC
§9.7); Railway keeps logs for its plan's retention window, and we would set
30 days in a production deployment.

## Scope cut and production gaps

### Cut on purpose

| Cut | Why | Where it would go |
|---|---|---|
| Caching (answers, extraction) | A repeated question is rare in an assessment, and a cache keyed on question text would serve a stale answer after a corpus or prompt change unless it is keyed on both versions too. | Key on (question, `corpus_version`, `PROMPT_VERSION`); measure the hit rate first |
| Streaming | `generate` is the bottleneck (above), but citations are validated only on the complete output, so a streamed answer can still turn into a refusal. | The next optimization (Cost & latency) |
| Chat history | One question, one answer (SPEC §1). Follow-ups would need the previous constraints carried over, i.e. a second extraction design. | — |
| Reranker | Recall@5 is 11/11 with eligibility applied first, and the model reads all three recipes it is given; nothing to rerank toward. | When recall@5 drops (ADR 001) |
| Embeddings, hybrid retrieval, vector DB | The ADR 001 trigger (recall@5 on paraphrase cases < 100 %) never fired; the held-out paraphrases also passed. | [ADR 001](adr/001-retrieval.md), "becomes invalid when" |
| Multi-language questions, accounts, nutrition, recipe scaling | Out of scope in SPEC §1. Nutrition questions are refused (`out_of_corpus`). | — |

### What production-grade would still need

- **Shared rate limiter.** The per-IP limit and the concurrency cap live
  in process memory, keyed on Railway's `X-Real-IP`; correct for one
  instance only. A second replica needs a shared store (Redis) or the
  edge's own limiter.
- **Multi-instance deploy.** One instance in one region; a deploy is a
  short restart. Needs at least two replicas behind Railway's
  health-checked rolling deploy, which in turn needs the shared limiter.
- **Staging environment.** One environment (production). A staging
  environment from the same `railway.ts`, deployed first and gated on the
  golden set, would catch a bad prompt before users do; today the smoke
  eval runs after production is already live.
- **Finer spend control.** The provider enforces a monthly cap ($30,
  workspace-wide) and the service answers 503 once it is reached. No
  daily or per-client budget, by decision: one client at the rate limit
  could spend the month's cap in about a day, and the service would then
  be down until the month ends or the cap is raised. Accepted for this
  deployment; a service with real traffic would add a daily budget.
- **Alerting.** Nothing pages anyone. Needs alerts on 5xx rate, p95
  latency over the SPEC §8 targets, invalid-output retries and the
  guarantee guard firing, from the per-request trace. The prerequisite
  is in place since stage 6b: every log line is JSON on stdout with its
  own level, and a request trace is `error` only on a 5xx, so an
  error-level alert in Railway means a failed request (before, Railway
  tagged every stderr line, traces included, as `error`).
- **Log retention.** Railway keeps logs for its plan's window; a
  production deployment would ship the trace to a store with 30-day
  retention (SPEC §9.7), which is also what replaying a bad answer needs.
- **Infrastructure outside the file.** Railway's generated domain cannot
  be declared in `.railway/railway.ts`, and the spend cap lives in the
  Anthropic Console.

### Known limitations of the answers

- The variant rule (SPEC §9.3) leaks into answers that have no variant:
  "X is not a variant of this dish, so it is not used here", sometimes
  naming a recipe that is not cited, and once calling a different soup
  "another version". One answer attributed our derived vegetarian label
  to the page. Wording only, no factual error
  ([factuality review](evals/factuality_review.md), notes 1–4).
- Extraction is unstable on two of 32 labelled questions (g02, x17: a
  dish word extracted as a required ingredient in some runs).
- The held-out set is five cases and the gate-refusal latency sample is
  nine requests; both are reported as measured, not extrapolated.

### Corpus and matching

- The ingredient alias table (`data/aliases.json`) is small and explicit
  (nut, dairy, egg, gluten for exclusion only, and the spellings the golden
  set needs; SPEC §4). An ingredient outside it matches only as written.
- Ingredient `normalized` names are heuristic; exclusion matching must also
  read `raw` (ADR 004).
- Pages whose diet category contradicts their ingredients are excluded,
  not repaired; the eligibility rule also ignores such a category
  (SPEC §4 v0.2).
