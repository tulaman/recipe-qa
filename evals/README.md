# Eval harness

Evaluation is layered (SPEC §7): extraction is measured on its own
labelled set, end-to-end runs go over HTTP to a running instance, and
retrieval recall is measured without generation (stage 5). Every runner
repeats each case, reports unstable cases as such, and writes a report
that records the versions it ran against.

```bash
# end to end, over HTTP (default: --set smoke --repeat 3)
uv run python evals/run.py --base-url http://localhost:8000
uv run python evals/run.py --base-url http://localhost:8000 --contract-only   # fake provider
uv run python evals/run.py --base-url https://... --set golden --milestone stage-5-golden

# extraction only, in process (needs ANTHROPIC_API_KEY; RECIPE_QA_PROVIDER=fake checks the runner)
uv run python evals/extract.py --set smoke
uv run python evals/extract.py --set golden --milestone stage-5-extraction
```

Both exit 0 only when every case passes in every run (and, end to end,
every endpoint check holds).

## Case sets

| Kind | File | Selected with |
|---|---|---|
| End to end | `evals/cases/<name>.json` | `run.py --set <name>` |
| Extraction | `evals/cases/extraction/<name>.jsonl` | `extract.py --set <name>` |

An end-to-end set is one JSON object:

```json
{
  "description": "what the set is for",
  "held_out": false,
  "cases": [
    {
      "id": "veg-under-30",
      "question": "A vegetarian dinner in under 30 minutes?",
      "expect": {
        "refused": false,
        "sources_any": ["southwestern-black-beans-and-rice"],
        "constraints": { "diet": "vegetarian", "max_total_minutes": 30 }
      }
    }
  ]
}
```

`expect` keys, all optional:

| Key | Check |
|---|---|
| `refused` | `refused` equals it. Also pins the case's expected outcome for the refusal matrix |
| `refusal_reason` | `refusal_reason` equals it |
| `sources_any` | at least one cited recipe ID is in the list |
| `constraints` | every cited recipe satisfies the predicate (below) |

An extraction set has one labelled question per line; `expect` is a
complete `Extraction` (`recipe_qa/schemas.py`), so omitted fields mean
their defaults (no time limit, no diet, no ingredients):

```json
{"id": "vegan-no-nuts", "question": "Vegan desserts without nuts?", "expect": {"in_domain": true, "safety_question": false, "diet": "vegan", "must_exclude": ["nut"]}}
```

Committed sets: `smoke` (both kinds, three questions). The golden set
lands in stage 5.

### Held-out set

A set with `"held_out": true` holds paraphrases written before tuning
(SPEC §7.3). The harness enforces what it can:

- **Kept apart**: every load of any set checks the whole `evals/cases/`
  directory; a question (case- and whitespace-insensitive) that appears in
  a held-out set and in any other set, extraction labels included, is an
  error. `tests/test_evals.py` runs the same check on the committed sets.
- **Committed**: a held-out set runs only with `--milestone NAME`, so its
  report lands in `evals/reports/milestones/`.
- **Once**: if a milestone report for that held-out set already exists,
  the run is refused. Re-running it means deleting a committed file, which
  is visible in review.

## Constraint predicates

`constraints` uses the `Extraction` field names and is checked against
corpus metadata (`data/recipes.jsonl`), never against the answer text:

| Key | A cited recipe passes iff |
|---|---|
| `max_total_minutes: N` | `total_minutes` is known and `< N` (`≤ N` with `time_limit_inclusive: true`) |
| `diet` | the recipe's `diet.<diet>` value is true; for `gluten_free` the status must be `explicit` (SPEC §4) |
| `must_include: [x]` | some ingredient line or normalized name contains `x` as a whole word, singular or plural |
| `must_exclude: [x]` | no ingredient, optional ones included, contains `x` that way |

An item can be a list of alternatives: `["nut", "walnut", "almond"]`.
Matching is by whole word, so `"nut"` does not match "walnuts"; the case
lists every word it means. This is deliberate: the predicate does not use
the service's alias table, so it can catch a wrong alias. Unknown keys or
diets make the set fail to load.

## Repeats and stability

Every case runs `--repeat` times (default 3). Per case:

| Verdict | When |
|---|---|
| `pass` | every run passed and every run had the same outcome |
| `fail` | no run passed and every run had the same outcome |
| `unstable` | anything else: the outcome changed between runs (whether the runs passed or failed), or some runs passed and others failed |

The outcome of an end-to-end run is `answer`, the refusal reason, or
`error` (non-200 or broken contract). Which recipes an answer cites may
vary between runs without making a case unstable; `sources_any` and
`constraints` decide whether that variation is acceptable. For extraction
the outcome is the whole extracted object. Unstable cases count as not
passing.

Rate limits (SPEC §8: 30 requests per minute per client) are waited out:
on HTTP 429 the runner sleeps 61 s and retries, up to twice, and records
the waits in the run. A 15-case set at three repeats crosses the limit.

## Refusal matrix

End-to-end reports (not `--contract-only`) count expected outcome ×
actual outcome over every run of every case that sets `refused`, and keep
four numbers apart:

| Count | Expected | Got |
|---|---|---|
| `false_answers` | a refusal | an answer |
| `false_refusals` | an answer | a refusal |
| `wrong_refusal_reason` | refusal for reason A | refusal for reason B |
| `technical_errors` | anything | non-200 or broken contract |

They are never summed into one error rate (SPEC §7.3).

The extraction runner counts errors per field and direction instead:
`false_positive` / `false_negative` for `in_domain` and `safety_question`;
`missed` / `spurious` / `wrong` for `max_total_minutes` and `diet`;
`missed` / `spurious` per ingredient for `must_include` / `must_exclude`
(order and case ignored); `time_limit_inclusive` only when a time limit is
labelled. A missed exclusion leads to a false answer downstream, a
spurious one to a false refusal.

## Reports and provenance

| Run | Written to | In git |
|---|---|---|
| exploratory | `evals/reports/<UTC stamp>-<kind>-<set>.json` | ignored |
| `--milestone NAME` | `evals/reports/milestones/NAME.json` | committed; never overwritten |

Every report starts with `provenance`; each entry says whether the
harness could confirm it:

| Entry | End to end | Extraction |
|---|---|---|
| `code` | build SHA from `GET /healthz` | this checkout's commit (+ dirty flag) |
| `prompts` | `PROMPT_VERSION` of this checkout; confirmed only if the server's build commit is in this repository and `recipe_qa/prompts/` here is identical to it | the version this process sent; confirmed |
| `model` | every `meta.model` seen | `provider.model` |
| `corpus` | `corpus_version` from `/healthz`; confirmed if it equals `data/manifest.json` | `data/manifest.json` |
| `set` | name, path, SHA-256 of the file, held-out flag, case count | same |
| `harness` | this checkout's commit; dirty if `recipe_qa/` or `data/` has local changes | same |

The service does not publish its prompt version, so over HTTP it is
derived from the build commit; `git fetch` makes a newer build
confirmable.

Each run also records, per request: HTTP status, client latency,
rate-limit waits, outcome, failures and the response body.

## Golden set structure

Concrete recipe IDs and expected facts are assigned in stage 5. The 15
cases cover:

| # | Scenario | Checks |
|---|---|---|
| 1 | How-to for a known dish by name | expected source cited, key steps present, no added steps |
| 2 | Semantic paraphrase without the dish name | expected source retrieved and cited |
| 3 | Ingredient question about a dish | fact matches the cited recipe |
| 4 | Two similar variants of one dish | one variant followed, other may be mentioned, both citable |
| 5 | Conflicting recipes for the same dish | no merged steps/ingredients |
| 6 | Vegetarian constraint | cited recipe passes the vegetarian rule |
| 7 | Under-N-minutes constraint | cited recipe has explicit time < N |
| 8 | Required ingredient (with alias) | cited recipe contains the ingredient |
| 9 | Excluded ingredient (negation, optional ingredient) | cited recipe contains none |
| 10 | Compound: time + diet + ingredient | all satisfied by one cited recipe |
| 11 | Unsatisfiable combination | `out_of_corpus`, no constraint relaxed |
| 12 | Known dish, absent fact (calories) | `out_of_corpus` |
| 13 | Out-of-domain question | `out_of_domain` |
| 14 | Allergen safety guarantee ("Is X nut-free?") | `safety`, citation to X |
| 15 | Factual allergen presence ("Does X contain nuts?") | answered from ingredients, no safety claim |

Content checks (`expected_facts`, `forbidden_claims`) and retrieval
recall@5 arrive with the golden set in stage 5.

Input validation, prompt injection, provider errors and invalid source IDs
are covered by `tests/`, not by the golden set, because they do not need a
live model. The harness's own logic is tested in `tests/test_evals.py`
against responses recorded from production
(`tests/fixtures/evals/recorded_responses.json`), with no network.
