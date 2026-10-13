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
- Rewrote: the first selection run used quotas only and produced 34 pages
  (pages count toward several groups); added a round-robin top-up to 50.
