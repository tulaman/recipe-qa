# ADR 001 — Retrieval: BM25 over whole recipes with deterministic metadata filters

Status: Proposed (2026-10-13). To be Accepted with measurements after the eval harness runs.

## Context

The corpus is 40–60 recipes (~25–40k tokens in total). Questions carry
hard constraints (time, diet, ingredient) that must be obeyed exactly, plus
free-text intent ("something creamy with pasta") that must find the right
dish. The assignment allows lexical, embedding or hybrid retrieval and asks
for the reasoning, with numbers where possible.

## Decision

1. The retrieval unit is a **whole recipe** (title + ingredients + steps +
   metadata), not ingredient/step chunks.
2. Hard constraints are applied by **code** over the whole corpus
   (`is_eligible`) *before* ranking, so a matching recipe can never be lost
   to a top-k cut-off.
3. Ranking is **BM25** over field-weighted text (title ×3, ingredients ×2,
   body ×1), in memory, built at startup from the committed snapshot.
4. No vector database. Dense embeddings are not used in v1; they are the
   first thing we add if the paraphrase cases in the golden set fail.

## Alternatives considered

| Option | Why not (for this corpus) |
|---|---|
| Chunk into ingredients / steps | Questions combine fields ("under 30 min *and* no eggs *and* how do I make it") — chunks would need re-assembly; whole recipes fit the context budget with room to spare (3 recipes ≈ 2–3k tokens). |
| Dense embeddings only | Cannot express hard constraints; nearest neighbour of "vegetarian under 30 minutes" is a semantically similar page, not a compliant one. |
| Hybrid BM25 + embeddings (RRF) | Measured need, not assumed: added only if recall@5 on paraphrase cases < 100 %. Costs ~40 min, an embedding call at ingestion, a `.npy` artifact in the repo. |
| Vector DB (pgvector, Qdrant, Chroma) | Another service, another secret, another failure mode in deploy, with zero benefit below thousands of documents. |
| Send the whole corpus to the model | ~30k tokens per request ≈ 50× the cost and ~3–5× the latency of top-3; makes grounding harder to verify. |

## Criteria

Recall@5 of the expected recipe on the golden set; p95 latency of the
retrieval stage; operational footprint (services, secrets, artifacts);
testability without a provider key.

## Measurements

Measured 2026-10-14 on corpus `976abf38…` (50 recipes); public rows
2026-10-15, build `5d8af05` on Railway.

| Measure | Value | Source |
|---|---|---|
| Recall@5, golden answerable cases (lexical, eligibility first) | **11/11 = 100 %** | `uv run pytest tests/test_retrieval_recall.py -s`, at commit `4b4be2c` and in CI |
| Recall@5 without the eligibility filter | 10/11: g08 ("garbanzo beans") misses, no page says "garbanzo" | stage 5a run of the same test, `docs/ai-notes.md` |
| Retrieve stage, answered questions (filter + BM25, 33 requests) | median 0.4 ms, p95 14.8 ms | `evals/reports/milestones/stage-5b-stages.json` |
| Retrieve stage, refusals (12 requests) | median 0.5 ms, p95 6.3 ms | same |
| Generate input tokens (3 recipes + constraints + prompt), answers | median 3,554, p95 4,543 | same |
| Held-out paraphrases answered with the expected recipe cited (h01–h03), run once | **3/3** | `evals/reports/milestones/stage-6-public-holdout.json` |
| Golden answerable cases with an expected recipe cited, public, 3 runs | 11/11 in every run | `evals/reports/milestones/stage-6-public-golden.json` |
| Retrieve stage, answered questions, public (33 requests) | median 0.2 ms, p95 9.4 ms | `evals/reports/milestones/stage-6-public-stages.json` |
| Generate input tokens, answers, public | median 3,554, p95 4,542 | same |

Hybrid retrieval was not tried: the trigger above (recall@5 on paraphrase
cases < 100 %) did not fire. The one recall miss so far was fixed by the
alias table, not by embeddings. Recall uses the labelled constraints, so
it isolates retrieval; an extraction error that adds a spurious include
still loses a recipe end to end (g05 in stage 5a, fixed in extraction).

## Consequences

- Retrieval is deterministic and unit-testable with no network.
- Docker image needs no provider key to build or to start.
- Paraphrase-heavy questions are the known weak spot; covered by golden
  cases 2 and 10.

## This decision becomes invalid when

- Recall@5 on paraphrase cases drops below 100 % on the golden set, or
  below 95 % on a larger eval → add hybrid (in-memory embeddings + RRF).
- Corpus grows past ~500 recipes or must update without redeploy → move
  the index out of process (SQLite FTS5 first, a vector store only when
  dense retrieval is already proven necessary).
- Recipes grow past ~2k tokens each so that top-3 no longer fits the
  context budget → split into field-level chunks with recipe-level
  re-assembly.
