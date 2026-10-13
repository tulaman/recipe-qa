# Eval harness

`run.py` sends the golden set to a running instance over HTTP, validates
every response against the contract (SPEC §2) and the case's expected
properties (SPEC §7), and writes `reports/<timestamp>.json` plus a summary
table to stdout. Exit code is non-zero when any hard check fails or fewer
than 13 of 15 content checks pass.

## Golden set structure

Concrete recipe IDs and expected facts are assigned after the corpus
snapshot is committed. The 15 cases cover:

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

Input validation, prompt injection, provider errors and invalid source IDs
are covered by `tests/`, not by the golden set, because they do not need a
live model.
