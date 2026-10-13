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

_To be filled from `evals/run.py` output: recall@5 on golden set (lexical,
and hybrid if tried); retrieval stage latency; token count of top-3
context._

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
