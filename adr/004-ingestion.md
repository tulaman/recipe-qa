# ADR 004 — Ingestion: pinned raw wikitext, offline parser, deterministic selection

Status: Accepted (2026-10-13), with the measurements below.

## Context

The corpus is 40–60 Wikibooks Cookbook pages (SPEC §6). Retrieval and
`is_eligible` depend on structured fields — ingredients, steps, time, diet —
that Wikibooks does not provide as data: they live in free wikitext, a
`{{Recipe summary}}` template used inconsistently, and user-maintained
categories. The snapshot must be reproducible, auditable, and testable
without network.

A probe of 1478 category members (soup, dessert, Italian, Indian,
Mexican, vegetarian, vegan, gluten-free) found:

- `time` on about half of the pages, in a dozen formats ("1½ hours",
  "15–20 minutes", "Prep: 10 minutes<br/>Cooking: 5 minutes per skillet",
  "2 hours to overnight");
- ingredient sub-sections ("=== Syrup ===") on ~13 % of pages, nested
  bullets with "Optional:" labels on a few;
- ~6 % of pages in a vegetarian/vegan category list meat, dairy, eggs or
  honey (French Toast with bacon in *Vegetarian recipes*).

## Decision

1. **Two phases.** `scripts/select_corpus.py` (network) picks pages and pins
   `revid` + `sha256` of the wikitext into `data/corpus_selection.json`,
   saving raw wikitext to `data/raw/`. `scripts/ingest.py` rebuilds
   `data/recipes.jsonl` and `data/manifest.json` from those two alone. A raw
   page is fetched by `revid` only if missing, and must match its hash.
2. **Parser in the package, pure.** `recipe_qa/ingestion/` turns wikitext
   into records with no I/O. `mwparserfromhell` handles templates and inline
   markup; page structure (headings, list items) is line-based.
3. **Provenance on every derived field.** `total_minutes`, `servings`,
   `difficulty` and each diet flag carry `explicit | derived | unknown` and
   the evidence string. Unclear time is `unknown`, never guessed.
4. **Categories from the pinned wikitext**, not the live API, so a rebuild
   is offline and stable.
5. **Deterministic selection.** Fixed anchors needed by the golden set,
   per-group quotas ordered by `sha256(seed + title)`, alternating timed and
   untimed pages, round-robin top-up to 50. A quality filter drops
   disambiguation and incomplete pages, pages with < 3 ingredients or < 2
   steps, pages over 8000 characters of recipe text, and pages whose diet
   category is contradicted by their own ingredients.
6. **Drift is a test failure.** `tests/test_corpus.py` rebuilds from
   `data/raw/` and compares byte for byte with the committed files.

## Alternatives considered

| Option | Why not |
|---|---|
| Regex-only wikitext parsing | Breaks on nested templates (`{{convert}}` inside ingredients, `[[File:...]]` inside the summary), case variants of template names and params. |
| Rendered HTML (`action=parse`) + an HTML parser | Template output is presentation (tables, spans), structure is harder to recover, and an extra request per page; categories still reflect current, not pinned, templates. |
| Live `prop=categories` | Reports the page's *current* categories (including template-added ones), so a rebuild from a pinned revision would not be reproducible. |
| Fetch on every build, no raw cache | Builds need network, can silently pick up edits, and parser tests cannot run on the real pages. |
| Hand-curated title list only | Not reproducible as a rule and invites cherry-picking; anchors are hand-picked only where a golden case needs a specific page, and each has a recorded reason. |
| LLM extraction of ingredients/time at ingestion | Non-deterministic, costs a key in CI, and the result could not be unit-tested; the parser is a few hundred lines with ~150 unit tests. |
| Trust diet categories as-is | ~6 % are contradicted by the page's own ingredients. SPEC §4 was revised (v0.2): the ingredient check always runs and overrides a contradicted category; selection also excludes such pages as low-quality data. |

## Measurements (snapshot `ea3bcd59…`)

- Candidate pool 1478 pages; 183 rejected by the quality filter; 50 selected.
- Time over the 484 timed pages of the first probe: 394 explicit, 37
  derived (summed stages), 53 unknown. In the corpus: 27 explicit,
  3 derived, 20 unknown.
- Vegetarian in the corpus: 25 explicit, 10 derived true, 15 derived false.
  Gluten-free: 7 explicit, 43 unknown (never derived).
- Coverage: soup 9, dessert 10, Italian 8, Indian 8, Mexican 8,
  vegetarian 13, vegan 13, gluten-free 7; overlap groups: carbonara (2),
  rice pudding (3), tomato soup (2), baklava (2), guacamole (2).
- `data/` is 444 KB including raw wikitext; parse + build of 50 pages
  takes well under a second.

## Consequences

- Everything after ingestion (retrieval, eligibility, evals) runs on
  committed files, with no network and no key.
- `corpus_version` hashes the parsed records, so a parser change that
  alters what the service sees changes the version even if no page changed.
- A contradicted diet category cannot make a recipe eligible (SPEC §4
  v0.2), independently of the selection filter, so a future refresh stays
  safe even if the filter is relaxed. A category still decides what the
  ingredient check cannot (unqualified stock, no parsed ingredient list).
- `normalized` ingredient names are heuristics. Inclusion matching uses
  them with aliases; exclusion must also look at `raw` (stage 4).

## This decision becomes invalid when

- The corpus must refresh without a redeploy → ingestion becomes a job that
  writes a versioned artifact the service loads, not a committed file.
- The corpus grows past a few hundred pages → the 8000-character cap and
  whole-recipe records need revisiting together with ADR 001.
- Parser `unknown` rates rise on new pages (e.g. > 30 % unknown time among
  pages that state one) → extend the rules, or add an LLM extraction pass
  whose output is reviewed and committed, never computed at request time.
