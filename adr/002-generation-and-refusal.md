# ADR 002 — Generation, grounding and refusal: LLM extracts, code decides, prompt answers

Status: Proposed (2026-10-13).

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

_To be filled: golden-set pass rates per category; extraction accuracy on
the 15 questions plus a small paraphrase set; token usage and cost per
answered / refused question; latency per stage._

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
