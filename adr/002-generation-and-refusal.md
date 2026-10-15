# ADR 002 — Generation, grounding and refusal: LLM extracts, code decides, prompt answers

Status: **Accepted** (2026-10-15). On the public URL: false answers 0,
false refusals 0, hard and content checks 15/15 in every golden run
([`stage-6-public-golden.json`](../evals/reports/milestones/stage-6-public-golden.json)),
held-out 5/5 run once ([`stage-6-public-holdout.json`](../evals/reports/milestones/stage-6-public-holdout.json)),
factuality review 0 / 0 / 0 ([`factuality_review.md`](../evals/factuality_review.md)),
$0.71 per 1,000 answered questions and client p95 4.58 s
([`stage-6-public-stages.json`](../evals/reports/milestones/stage-6-public-stages.json), README).
Proposed 2026-10-13.

## Context

The service must answer only from retrieved recipes, obey constraints,
refuse machine-readably, and be careful about allergies. An LLM is good at
reading a question and bad at being held to a rule; code is the opposite.
The design question is where to draw the line.

## Decision

Two provider calls per answered question, both with structured output:

1. **`extract_constraints`** (small prompt, ~400 tokens): returns
   `Constraints` plus `in_domain` and `safety_question` flags. The model
   reads natural language; it does not decide eligibility.
2. **`generate`** (question + constraints + up to 3 full recipes): returns
   `{answerable, answer, source_ids}`. The model may only cite IDs it was
   given; it is told to set `answerable=false` when the recipes do not
   contain the asked fact.

Everything between and after is code: domain/safety gates, `is_eligible`
over the whole corpus, candidate ranking, validation of `source_ids`
against the candidate set, mapping IDs → canonical title/URL, invariants
I1–I5, HTTP status mapping.

Refusal policy and the allergy decision are specified in SPEC §5 and are
the main thing this ADR exists to defend: *guarantee* questions are refused
with `safety` **but keep citations**, so a refusal still helps the user
check the ingredient list themselves.

Model: Claude Haiku 5.5 (list price $0.10 / $0.50 per MTok on 2026-10-13).

## Alternatives considered

| Option | Why not |
|---|---|
| Regex / rule-based constraint parser | Cheap and unit-testable, but every phrasing ("no dairy please", "I'm vegan", "quick weeknight meal") is a new rule; in a 7-hour budget it is the hour we do not have. Kept as the documented fallback if extraction proves unreliable in evals. |
| One combined call (extract + answer) | Saves ~300–500 ms and ~$0.0001, but the constraint filter must run *before* retrieval, so one call would have to see the whole corpus — see ADR 001. |
| Let the prompt enforce constraints | Not verifiable; a model that "mostly" respects "under 30 minutes" is a product bug we could not detect without reading every answer. |
| Refuse all allergy questions, no citations | Safer for us, worse for the user: a refusal with the recipe linked lets them read the ingredient list; a bare refusal does not. |
| Larger model (Sonnet 5.5) by default | 20× the price for a task whose hard parts are already in code. Switching condition recorded below. |

## Criteria

False-answer rate (answered when it should refuse) weighted higher than
false-refusal rate; constraint violations in cited recipes (must be zero);
cost per 1,000 questions; p95 latency; what can be unit-tested without a
provider.

## Measurements

Measured 2026-10-14, `claude-haiku-5-5`, corpus `976abf38…`, build
`ad4a74a` (prompts confirmed by the harness), 3 runs per case, served
from a clean export of the commit. Public measurements of the final
build follow at the end of this section.

**Extraction**, 32 labelled questions × 3 runs = 96 runs per field. Runs
with the field right, stage 4b baseline → stage 5b
(`evals/reports/milestones/stage-4b-extraction-baseline.json` →
`stage-5b-extraction.json`):

| Field | 4b | 5b | 5b errors |
|---|---|---|---|
| `in_domain` | 96/96 | 96/96 | — |
| `safety_question` | 90/96 | 96/96 | — (4b: 6 false positives) |
| `max_total_minutes` | 96/96 | 96/96 | — |
| `time_limit_inclusive` | 96/96 | 96/96 | — |
| `diet` | 96/96 | 96/96 | — |
| `must_include` | 73/96 | 93/96 | 5 spurious items in 3 runs (g02 mango, yogurt ×2; x17 salsa ×1) |
| `must_exclude` | 93/96 | 96/96 | — |
| Cases passing every run | 23/32 | 30/32 | g02, x17 unstable |

One golden question is mis-parsed in some runs (g02), which is at, not
past, the invalidation limit below.

**Refusal matrix**, golden, 45 runs (`stage-5b-golden.json`):

| Expected \ Got | answer | out_of_corpus | out_of_domain | safety | error |
|---|---|---|---|---|---|
| answer | 33 | 0 | 0 | 0 | 0 |
| out_of_corpus | 0 | 6 | 0 | 0 | 0 |
| out_of_domain | 0 | 0 | 3 | 0 | 0 |
| safety | 0 | 0 | 0 | 3 | 0 |

False answers 0, false refusals 0, wrong reason 0, technical errors 0.
Hard checks 15/15 in every run; content checks 15/15 in each of the three
runs. The guarantee-wording guard (SPEC §5) fired 0 times.

**Latency and tokens per stage**, server trace of the same 45 runs, all
matched by request ID (`stage-5b-stages.json`); median / p95:

| Stage | Answers (33) | Refusals (12) |
|---|---|---|
| extract, ms | 1,193 / 1,953 | 1,080 / 1,488 |
| retrieve, ms | 0.4 / 14.8 | 0.5 / 6.3 |
| generate, ms | 1,996 / 3,105 | 970 / 1,081 (only g12, refused by the model) |
| total (server), ms | 3,282 / 4,695 | 1,124 / 2,459 |
| extract tokens in / out | 1,641 / 65 (p95 1,650 / 74) | 1,641 / 19 (p95 1,653 / 73) |
| generate tokens in / out | 3,554 / 252 (p95 4,543 / 550) | 3,602 / 25 |

At the list prices above, the median answered question is about
5,200 input and 320 output tokens, ≈ $0.00068, so ≈ $0.68 per 1,000
answered questions. That is under the $1.00 target but above the $0.55
SPEC §8 projection: extraction input (1.6k tokens, mostly the output
schema) is three times the ~400 estimated. Gate refusals (out_of_domain,
safety) skip generation. Over the same runs, client-side p95 for answers
is 4.7 s (target 5 s).

Golden by category across stages, rescored offline against the current
set (`uv run python evals/rescore.py evals/reports/milestones/*-golden*.json`):
see `docs/ai-notes.md`, stage 5b.

**Public, final build** (2026-10-15, build `5d8af05`, same prompts
`d259a00c2700`, on https://recipe-qa-production.up.railway.app):

| Measure | Value | Report |
|---|---|---|
| Golden × 3, refusal matrix | identical to the table above: 33 answers, 6 / 3 / 3 refusals as expected; false answers 0, false refusals 0, wrong reason 0, errors 0 | `evals/reports/milestones/stage-6-public-golden.json` |
| Golden content checks per run | 15/15, 15/15, 15/15 | same |
| Held-out, run once | 5/5 cases; 3 answers, `out_of_corpus` and `safety` refusals as expected | `evals/reports/milestones/stage-6-public-holdout.json` |
| Factuality review, 33 answers | added steps 0, invented time or quantity 0, merged recipes 0 | `evals/factuality_review.md` |
| Answers, server median / p95 | extract 997 / 1,634 ms, generate 1,950 / 2,957 ms, total 3,099 / 4,247 ms | `evals/reports/milestones/stage-6-public-stages.json` |
| Answers, tokens median | extract 1,641 in / 65 out, generate 3,554 in / 314 out | same |
| Cost per 1,000 answered questions | $0.71 at medians, $0.90 at p95 tokens (formula in README) | same |
| Answers, client p95 | 4.58 s (33 requests; target 5 s) | `stage-6-public-golden.json` |

## Consequences

- Constraint application is fully tested without a provider key.
- Extraction quality is a measured, not guaranteed, property; the eval
  harness is the only proof.
- Two calls instead of one: ~+400 ms latency, ~+10 % cost.

## This decision becomes invalid when

- Extraction mis-parses > 1 of 15 golden questions after prompt tuning →
  add the rule-based parser as a pre-pass and use the model only for what
  it misses, or switch the extraction call to a stronger model.
- Content-check pass rate < 13/15 with Haiku after prompt fixes → move
  `generate` to Sonnet 5.5 and re-measure cost (expected ≈ $8–10 per
  1,000).
- A product requirement to answer nutrition or safety questions appears →
  this needs a curated allergen dataset, not a prompt change.

Stage 6 notes: the extraction condition is at its limit, not past it
(one golden question, g02, mis-parsed in some runs, stage 5b). The
content condition held: 15/15 in every public run. The spend cap is
not part of this decision; a provider refusing for spend answers HTTP
400, which stage 6 found mapped to 500 and stage 6b maps to 503 (SPEC
§2.3 v0.7).
