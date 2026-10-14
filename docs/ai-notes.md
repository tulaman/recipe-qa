# AI usage notes

How AI coding agents were used on this assignment, what was accepted,
what was rewritten. Updated as work progresses; entries are dated.

Tools: Claude (claude.ai, model `claude-fable-5-1` session) for planning,
drafting documents and code; the repository's `CLAUDE.md` holds the
standing instructions given to the agent. Prompts sent to the runtime model
live in `recipe_qa/prompts/`.

## Workflow

1. Assignment PDF → a written critique of a first draft plan → revised plan
   (kept outside the repo).
2. SPEC.md drafted by the agent from the plan, reviewed and edited by me
   before the first commit.
3. For each stage: tests first where the plan says so, implementation,
   eval, documents updated in the same stage.

## Log

### 2026-10-13 — stage 0/1

- Probed the MediaWiki API with a throwaway script (not committed). Found
  that only ~20 % of recipes carry an explicit `Time`; this changed the
  corpus-selection rule in SPEC §6 and assumption §9.8.
- Accepted: SPEC structure, invariants I1–I5, two-call generation design.
- Rewrote: the first draft treated all refusals as "no citations"; changed
  so that `safety` refusals keep citations (SPEC §5, ADR 002).
- Rewrote: the first draft placed CI/CD as a late stage; moved minimal CI
  to the first tests and the deploy workflow to the first deploy.

### 2026-10-13 — stage 2: ingestion and full corpus

- Chose between three parsing approaches (regex, rendered HTML,
  `mwparserfromhell` + line structure) after scanning ~1000 candidate pages
  with throwaway probes; recorded in ADR 004.
- Tests first for each layer (time, ingredient lines, whole page, diet,
  records, corpus snapshot); each failing-test commit precedes its
  implementation.
- Accepted: the agent's time-parsing rules after running them over 484
  real `time` values and reviewing the 53 unknowns; two rules were
  loosened (quantified "Chilling: 4 hours" stages, malformed `</br>`), the
  rest stayed conservative.
- Found by running the derived diet check against category labels: ~6 %
  of vegetarian/vegan pages contradict their ingredients. Did not change
  the SPEC §4 rule silently; excluded those pages at selection, added a
  corpus test, and left the rule change as an open question.
- Decision (mine, after review): revise SPEC §4 to v0.2 — the ingredient
  check always runs and overrides a contradicted category. Committed as a
  SPEC change first, then failing tests, then the implementation.
- Rewrote: the first selection run used quotas only and produced 34 pages
  (pages count toward several groups); added a round-robin top-up to 50.

### 2026-10-13 — stage 3: vertical slice of POST /ask and first deploy

- Tests first for the HTTP contract, the response invariants and BM25
  (one failing-test commit), then schema, retrieval, providers, pipeline.
- Accepted: the agent's two-call pipeline exactly as SPEC §3 describes,
  although the plan for this stage said "one generation call": the
  out_of_domain checkpoint needs the extraction call anyway, and building
  it now avoids rewriting the pipeline in stage 4. Constraint fields are
  extracted but not yet enforced.
- Found while building: two SPEC §3 steps were implicit (which recipes a
  safety refusal cites; what happens when BM25 matches nothing). Clarified
  in SPEC v0.3 before the implementation commit.
- Rewrote: the first schema draft had developer notes in the pydantic
  docstrings; the SDK sends those to the model as schema descriptions, so
  they became comments.
- Rewrote: nutrition sections were first left to the prompt ("never
  answer nutrition"); moved to code — they are never rendered to the
  model.
- Model settings come from the bundled Claude API reference, not memory:
  `claude-haiku-5-5`, structured outputs via `messages.parse`, effort
  `low`, no sampling parameters (Haiku 5.5 rejects non-default ones).


### 2026-10-14 — IaC: `railway.json` → `.railway/railway.ts`

- I questioned `railway.json` as "config, not IaC, and deprecated". The
  agent (Claude Code, `claude-opus-5-5`) confirmed it from Railway's docs,
  then tested the consequence instead of assuming it: `railway config
  migrate status` showed no service reads the file, and `railway config
  pull` showed the live service had no health check and no restart policy.
  Stage 3 had written the file without checking that Railway applied it;
  the lesson is recorded in ADR 003's amendment.
- Accepted: Railway IaC in TypeScript, applied inside `deploy.yml` rather
  than in Railway's separate plan/apply workflow (one path to production);
  destructive applies only by a manual run with `allow_destructive`.
- Changed: the stage 3 `GIT_SHA` service variable moved into the image
  (`recipe_qa/BUILD_SHA`, tests first). A file the IaC does not own is
  simpler than a variable changed on every deploy, and runtime variables
  override the image, so an old container could report a new commit.
- The `tsc` check added for `railway.ts` caught that the CLI-generated
  `variables: { managed: true }` is not in the SDK's types; a plan of a
  copy without it showed managed variables are the CLI default, so the
  line was dropped.
- Review (Codex bot on PR #3) flagged that an explicit `ON_FAILURE`
  restart type never converges in the plan (railwayapp/cli#1184), which
  would have failed every deploy at the empty-plan check. Verified against
  the issue and Railway's documented defaults before changing anything;
  only the default-valued field is affected, so it is now left implicit.

### 2026-10-14 — stage 4a: layered evaluation

- SPEC v0.4 first (requirements only, no file names): extraction measured
  separately, false answers and false refusals never summed, stability
  across repeats, a held-out set run once, versioned reports. Then
  failing harness tests, then the harness.
- Tests replay `/ask` bodies recorded from production (the deployed smoke
  run), not hand-written ones, so the harness is checked against what the
  service really returns.
- Decision: constraint predicates live in `evals/`, not in `recipe_qa`,
  and do not use the service's alias table; a case lists its own aliases.
  An oracle that shares the code under test cannot catch its mistakes.
- Decision: the service does not publish its prompt version, and adding it
  to `/healthz` would change the API contract in a task that excludes
  pipeline changes. The harness derives it instead: if the build commit
  `/healthz` reports exists locally and `recipe_qa/prompts/` is identical
  there, the local `PROMPT_VERSION` is the server's; otherwise the report
  says "unconfirmed".
- Found while designing: three repeats of a 15-case set exceed the
  service's own 30 requests/minute limit, so the runner waits out a 429
  instead of recording it as a failure.

### 2026-10-14 — stage 4b: golden, held-out and extraction sets; baseline

- Sets first, fitted to the corpus rather than to the model: every
  recipe ID, competitor and expected fact comes from `data/`, and
  `tests/test_golden_sets.py` checks each scenario claim (a competitor at
  exactly 20 minutes, raisins only optional in a competitor, one recipe
  for the compound case, none for the unsatisfiable one). Expected facts
  are matched against the ingested visible text; the raw wikitext splits
  phrases with links ("risotto [[Cookbook:Rice|rice]]").
- Changed from the task, by the user's decision: the task asked for the
  hold-out questions in the extraction set. SPEC §7.3 and `check_disjoint`
  forbid a held-out question in any other set, and running extraction on
  it would be running the hold-out. The extraction set is the 15 golden
  questions plus 17 extra phrasings; the hold-out (5 cases) was not run.
- Labelling conventions written down in `evals/README.md` before the
  run (dish words are not `must_include`; ingredients in the user's words;
  no invented time for "quick"; guarantee questions carry no constraints).
- Baseline, nothing fixed. Build `2096155` (prompts `c9e30cb2fb92`,
  `claude-haiku-5-5`, corpus `976abf38…`), local, served from a clean
  export of the commit so every provenance entry is confirmed. Reports:
  `evals/reports/milestones/stage-4b-{golden,extraction}-baseline.json`.
- Golden, 3 runs: 13/15 pass, 1 fail, 1 unstable. Over 45 runs: false
  answers 0, false refusals 3, wrong reason 0, technical errors 1. Content
  checks are recorded in the cases but not measured yet.
  - g08 (garbanzo beans) fails 3/3 as a false refusal. Extraction is
    right (`garbanzo bean`), but no page says "garbanzo": BM25's top five
    are other recipes (Indian Beans first), neither chickpea recipe
    reaches the model, and it correctly finds nothing to answer from.
    Needs the alias table (SPEC §4).
  - g09 (rice pudding without raisins) is unstable: two runs cite Rice
    Pudding, whose raisins are optional ("leave them out"), which SPEC §4
    counts as present; one run was a 503 when generation hit the 12 s
    deadline. The extraction run was calling the same API key at the time,
    which may have slowed the provider; the error is recorded as measured.
  - The other constraint cases (6, 7, 10, 11) pass without `is_eligible`:
    retrieval and the model happened to pick eligible recipes. A pass
    there is not yet a guarantee.
  - Latency (client side, 29 answered runs): p50 3.1 s, p95 4.6 s, max
    4.9 s; gate refusals max 1.3 s. No 429 waits: sequential requests at
    ~3 s each stay under 30 per minute, unlike the stage 4a estimate.
- Extraction, 3 runs: 23/32 pass, 8 fail, 1 unstable. `in_domain`,
  `diet`, `max_total_minutes` and `time_limit_inclusive`: no errors over
  96 runs. `must_include` spurious 29, `must_exclude` spurious 3,
  `safety_question` false positive 6.
  - Spurious includes are dish words ("garlic" for the garlic soup,
    "pasta" for carbonara, "pancake", "salsa"). Harmless where the dish
    contains the word, but "pancake" or "salsa" as a required ingredient
    would filter out the dish once `is_eligible` exists: a future false
    refusal.
  - `safety_question` false positives are "an egg-free pancake recipe"
    and "I can't have onions": requests for recipes read as guarantee
    questions. Both would be false refusals end to end.
  - "Is Henry Thiele's pancake egg-free?" also gets `must_exclude: egg`,
    against the convention; harmless, since the safety gate runs first.

### 2026-10-14 — stage 5a: eligibility over the whole corpus

- Spec first: the alias table's scope is recorded in SPEC §4 (v0.5)
  before the table grew. It is directional ("no nuts" excludes almonds,
  "with almonds" does not match walnuts), holds nut, dairy, egg and the
  three spellings the golden set needs (garbanzo, yoghurt, sultana), and
  lists gluten sources for exclusion only. They are never used for
  `gluten_free`, which still needs an explicit category.
- `why_ineligible` returns every failed constraint, not the first one, so
  the trace shows that nothing was dropped. Time and diet read the facts
  ingestion already derived (a range is stored at its upper bound), so the
  §4 rules live in one place. "No dairy" reuses the diet check's look-alike
  rule, so "coconut milk" and "butter beans" are not dairy in either.
- Step 5 filters all 50 recipes before BM25. The test's premise is checked
  on the corpus: for "What rice dish can I make in under 30 minutes?"
  (vegan, < 30, rice) the only eligible recipe is 8th unfiltered.
  An empty filter refuses `out_of_corpus` without a generation call, and
  the message names the constraints.
- Recall@5 (`tests/test_retrieval_recall.py`, labelled constraints, no
  network): 11/11. Without the filter g08 misses, because no page says
  "garbanzo".
- Golden, 3 runs, build `cd89004` (prompts `c9e30cb2fb92` unchanged,
  `claude-haiku-5-5`, corpus `976abf38…`). Served from a clean export of
  the commit, so all provenance is confirmed. Report:
  `evals/reports/milestones/stage-5a-golden.json`. 15/15 pass, 0 unstable.
  Over 45 runs: false answers 0, false refusals 0, wrong reason 0,
  technical errors 0. Constraint correctness holds in every run of
  g06–g10.
  - g08 flipped fail → pass: the alias makes both chickpea recipes
    eligible. Only Qabuli is cited, though, because Spicy Chickpea Soup
    shares no term with "What can I cook with garbanzo beans?". BM25 never
    returns a zero-score recipe (SPEC §3 step 6). A question whose only
    eligible recipes share no word with it would refuse `out_of_corpus`
    even though a recipe is eligible. No golden case hits this; it is
    recorded, not changed.
  - g09 flipped unstable → pass: Rice Pudding (optional raisins) and Kheer
    (sultanas) are filtered out, and Italian Rice Pudding is cited in all
    three runs. The baseline 503 did not recur.
  - g11 still passes, now for a deterministic reason. The code refuses
    before generation ("none of them matches all of: vegan; under 30
    minutes; with potato"), so it no longer depends on the model judging
    the recipes unanswerable.
  - g05 passes but cites only Carbonara Pasta in every run. The extractor
    adds `must_include: pasta` for "pasta carbonara" (stage 4b), and
    Spaghetti alla Carbonara lists "spaghetti", so it is filtered out.
    Constraints are not relaxed (§4); the fix belongs in extraction. The
    same spurious includes ("pancake", "salsa") in the extraction set
    would be false refusals end to end.
  - Latency (client side, 33 answered runs): p50 3.3 s, p95 4.3 s, max
    5.1 s. Refusals max 2.8 s.
- Review fixes after the run (PR #9): the image now ships
  `data/aliases.json` (the container failed to start without it), and
  matching folds accents on both sides ("crème" matched nothing) and knows
  -f/-ves and -y/-ys plurals ("bay leaf" missed "bay leaves"). No golden
  constraint uses an accented or irregular term, and recall@5 is still
  11/11, so the run on `cd89004` stands.

### 2026-10-14 — stage 5b: refusal policy, grounding, content checks

- The task assumed cases 4, 5, 12, 14, 15 and some content checks were
  still open after 5a. The 5a report said 15/15, but that number was hard
  checks only: `check_expectations` never read `expected_facts` or
  `forbidden_claims`. Raised before planning; decided with the user:
  content becomes its own per-run column, old reports are rescored
  offline (`evals/rescore.py`), milestone files are never rewritten.
- Rescoring found a regression nothing had caught. In 5a g05 cited only
  Carbonara Pasta in every run: the extractor added `must_include: pasta`,
  and `is_eligible` dropped Spaghetti alla Carbonara ("spaghetti").
  `sources_any` accepted either variant. Decided with the user: SPEC §9.3
  becomes binding (v0.6, "mention and cite the other variant"), g04/g05 get
  `sources_all`, and the fix goes into extraction, the layer that failed.
- "Never the word safe": the agent first proposed a global ban. Rejected
  in discussion, because the corpus says "heat-safe tins" and "refrigerated
  safely". Accepted design: a prompt rule with an allowed phrasing ("the
  ingredient list on the source page does not mention X") plus a code
  guard scoped to `must_exclude` answers (any form of *safe*, *-free*,
  *free of/from* → invalid output → one plain retry → 502). A retry that
  tells the model why was deferred until the trace shows the guard firing.
  It fired 0 times in every run after the prompt rule.
- Safety branch: behaviour already matched SPEC §5 and is now pinned by
  tests (both baklavas cited, no generation, message ≤ 2 sentences). The
  one gap was its retrieval time, missing from the trace; fixed.
- Generation never received the constraints (ADR 002 said it did):
  `generate_message` now renders a `<constraints>` block, test first.
- One prompt change per commit, each followed by golden ×3 and extraction
  ×3 from a clean export. A report was committed whenever a case flipped
  (`stage-5b-f{3,6,7,8,4}-*.json`):

  | Commit | Change | Golden content per run | Extraction | Flips |
  |---|---|---|---|---|
  | `b134d62` (pre) | harness + guard, prompts as 5a | 14, 14, 14 | 23/32 | — (exploratory) |
  | `a1ce046` | generate: constraints, provenance | 13, 14, 14 | 23/32 | g04 pass → unstable (one variant cited) |
  | `05f0980` | generate: always cite the other variant | 14, 14, 14 | 23/32 | g04 → pass |
  | `c6f32e3` | extract: dish words are not includes | 15, 15, 15 | 28/32 | g05 → pass; extraction g14, x05, x06 → fail/unstable |
  | `1dcfbde` | extract: a guarantee has no constraints | 15, 15, 15 | 30/32 | g14, x05, x17 → pass; g02 → fail |
  | `0af6664` | generate: "not mentioned" wording | 15, 15, 15 | 30/32 | extraction g03 → unstable (extract.md unchanged) |
  | `ad4a74a` | generate: absent fact, times, no asides | 15, 15, 15 | 30/32 | g02 → unstable, x17 → unstable (extract.md unchanged) |

- Golden by category, rescored against the current set (v0.6, with
  `sources_all`); runs passing / 45:

  | Category | Baseline 4b (`2096155`) | After 5a (`cd89004`) | After 5b (`ad4a74a`) |
  |---|---|---|---|
  | Contract | 44 | 45 | 45 |
  | Refusal | 41 | 45 | 45 |
  | Sources | 39 | 45 | 45 |
  | Constraints | 42 | 45 | 45 |
  | Content | 38 (12, 13, 13 per run) | 42 (14, 14, 14) | 45 (15, 15, 15) |
  | Cases passing hard checks 3/3 | 13/15 | 15/15 | 15/15 |
  | Extraction cases, 3/3 | 23/32 | not run (prompts as 4b) | 30/32 |

  4b and 5a were scored against an older golden file; their content
  column is what they would have scored under the current rules.
- Final run: `evals/reports/milestones/stage-5b-golden.json`, `-extraction`,
  `-stages`. Hard 15/15 in every run, content 15/15 in every run, no
  unstable golden case. Extraction unstable: g02, x17. Latency, client
  side: answers p50 3.3 s / p95 4.7 s; refusals p50 1.1 s / p95 2.5 s.
  The held-out set has not been run.
- Accepted as generated: the guard regex, the stages join, the rescore
  tool, all prompt wording after review. Corrected before committing: a
  stages test expected median 3,600 where (2,500 + 4,800) / 2 is 3,650.
  Extraction prompt examples were written to avoid golden dishes, so the
  rule is not fitted to the questions it is measured on.
- For the factuality review, not caught by any check: in the last run a
  g06 answer called Le Tourin a "variant-free garlic soup" and said Spicy
  Tomato Soup "is also vegetarian" (a derived value stated as a label);
  a g07 answer called Agnolotti in Brodo "a variant" of another soup. The
  variant rule leaks the word "variant" into answers that have none.
