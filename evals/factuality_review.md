# Factuality review (SPEC §7.2)

A manual read-through of every answer in the latest public golden report
against the page(s) it cites. The automated content checks look for
required and forbidden phrases; this review looks for what a regex cannot
see: steps the page does not have, times or quantities the page does not
state, and two recipes merged into one procedure.

- Report: [`reports/milestones/stage-6-public-golden.json`](reports/milestones/stage-6-public-golden.json)
  (build `5d8af05`, prompts `d259a00c2700`, `claude-haiku-5-5`, corpus
  `976abf38…`, https://recipe-qa-production.up.railway.app, 2026-10-15).
- Scope: the 11 answerable golden cases × 3 runs = 33 answers. The four
  refusal cases (g11–g14) have no answer text to review.
- Page text: `data/recipes.jsonl` (what the model was shown), parsed from
  the pinned revisions in `data/raw/<id>.wiki`. Times and servings were
  also checked against the raw `recipesummary` template.
- Reviewed by: the agent (Claude Code, `claude-opus-5-5`), every answer
  read in full next to the page; the table is the record the author
  reviews in the pull request.

Columns: **Steps** = steps not on the page, **Time/qty** = a time or
quantity not on the page, **Merged** = steps or ingredients of two
recipes combined into one procedure. `n` = no, `y` = yes.

## Result

| Case | Run | Cited | Steps | Time/qty | Merged | Evidence |
|---|---|---|---|---|---|---|
| g01 | 1 | le-tourin-garlic-soup | n | n | n | 7 steps in page order; 30 min / serves 2 = `recipesummary` |
| g01 | 2 | le-tourin-garlic-soup | n | n | n | Same 7 steps with page quantities (10–12 cloves, 1 tbsp, 4 cups, 1 tsp) |
| g01 | 3 | le-tourin-garlic-soup, white-bean-soup-… | n | n | n | Le Tourin steps only; white bean soup named as "another version" (white beans, chicken broth: correct, but not a variant; see note 3) |
| g02 | 1 | sweet-mango-lassi | n | n | n | 3 steps, 4 quantities, "does not state a total time" (page has none) |
| g02 | 2 | sweet-mango-lassi | n | n | n | Same 3 steps; serves 6 = page |
| g02 | 3 | sweet-mango-lassi | n | n | n | Quantities folded into the 3 steps, all from the ingredient list |
| g03 | 1 | chicken-and-pasta-alfredo | n | n | n | 1½ oz Parmigiano-Reggiano, 4 oz mozzarella = ingredient lines 3 and 6 |
| g03 | 2 | chicken-and-pasta-alfredo | n | n | n | Same |
| g03 | 3 | chicken-and-pasta-alfredo | n | n | n | Same (spelling normalized to "Parmigiano") |
| g04 | 1 | guacamole-i, chunky-guacamole | n | n | n | Guacamole I steps 1–7; Chunky named apart with its own 3 ingredients |
| g04 | 2 | guacamole-i, chunky-guacamole | n | n | n | 10 min (Guacamole I) and 30 min (Chunky, "½ hour") each from its own page |
| g04 | 3 | guacamole-i, chunky-guacamole | n | n | n | Guacamole I steps; Chunky summarized as avocado, lime juice, salsa fresca |
| g05 | 1 | spaghetti-alla-carbonara, carbonara-pasta | n | n | n | Method is Spaghetti alla Carbonara steps 1–8 only; Carbonara Pasta described apart (note 4) |
| g05 | 2 | spaghetti-alla-carbonara, carbonara-pasta | n | n | n | Same method; variant: "4 eggs", crisp pancetta, lemon zest, thyme = its page |
| g05 | 3 | spaghetti-alla-carbonara, carbonara-pasta | n | n | n | Same method; variant: yolks with cooled pancetta, served immediately = its steps 4, 8 |
| g06 | 1 | mexican-tomato-soup, le-tourin-…, spicy-tomato-soup | n | n | n | 10 min / serves 6 = `recipesummary`; says "the page marks it as vegetarian" (note 1) |
| g06 | 2 | mexican-tomato-soup, le-tourin-… | n | n | n | Ingredient summary matches the 10 lines; Le Tourin described from its own page |
| g06 | 3 | mexican-tomato-soup, le-tourin-…, spicy-tomato-soup | n | n | n | Steps 1–6 summarized; "vegetarian by derivation" (correct attribution) |
| g07 | 1 | mexican-tomato-soup, agnolotti-… | n | n | n | 10 min and 15 min, each from its own page; Agnolotti not followed |
| g07 | 2 | mexican-tomato-soup, agnolotti-… | n | n | n | 6 steps in page order; "½ teaspoon salt" = step 3 |
| g07 | 3 | mexican-tomato-soup | n | n | n | Ingredients only; mentions Agnolotti without citing it (note 2) |
| g08 | 1 | qabuli-central-asian-rice-pilaf | n | n | n | 7 steps; "vegetarian, vegan and gluten-free as stated" = explicit categories |
| g08 | 2 | qabuli-central-asian-rice-pilaf | n | n | n | 1 kg rice, 3 onions, 500 g chickpeas = ingredient list; 8–10 min = step 4 |
| g08 | 3 | qabuli-central-asian-rice-pilaf | n | n | n | Same steps; ½ cup cilantro, 4–5 chiles = ingredient list |
| g09 | 1 | italian-rice-pudding | n | n | n | 8 steps verbatim with quantities; 1½ h / serves 8 = `recipesummary` |
| g09 | 2 | italian-rice-pudding | n | n | n | Same 8 steps; names Southwestern Black Beans and Rice as "not used" (note 2) |
| g09 | 3 | italian-rice-pudding | n | n | n | Same 8 steps; "2 cups (500 ml)" = ingredient line 1 |
| g10 | 1 | southwestern-black-beans-and-rice | n | n | n | 20 min / serves 4 = `recipesummary`; 4 ingredients with page sizes |
| g10 | 2 | southwestern-black-beans-and-rice | n | n | n | Same |
| g10 | 3 | southwestern-black-beans-and-rice | n | n | n | Same |
| g15 | 1 | kashmiri-pulao | n | n | n | 50 g each of cashew, pistachio, almonds = ingredient lines 14–16 |
| g15 | 2 | kashmiri-pulao | n | n | n | Same; names Apple Crisp I and Baklava II as "not mentioned" (note 2) |
| g15 | 3 | kashmiri-pulao | n | n | n | Same |

**Summary: 33 answers, 0 with added steps, 0 with an invented time or
quantity, 0 with merged recipes.** Four notes below are wording issues,
not factual errors.

## Notes (outside the three criteria)

1. **Attribution of a derived label** (g06 run 1). "The page marks it as
   vegetarian by checking that none of its 10 ingredients is a meat or
   fish" describes our corpus derivation (`metadata.diet.vegetarian`,
   status `derived`), not the page, which has no vegetarian category. The
   claim itself is true; who made it is wrong. Run 3 of the same case says
   "vegetarian by derivation", which is right.
2. **Narrated candidate filtering** (g07 runs 1 and 3, g09 run 2, g15
   run 2). The answer names a recipe it was shown but does not use ("…is
   not a variant of this dish, so it is not used here"). Sometimes the
   named recipe is not cited (g07 run 3, g09 run 2, g15 run 2), so the
   user sees a dish name without a link. The variant rule from stage 5b
   leaks into answers that have no variant; already observed in stage 5b
   (`docs/ai-notes.md`).
3. **A different dish called "another version"** (g01 run 3). White Bean
   Soup is cited as another version of Le Tourin; it is another garlic
   soup, not a variant. Same cause as note 2.
4. **Imprecise summary of the second variant** (g05 run 1). Carbonara
   Pasta is summarized as tossing the sauce "with hot drained pasta"; its
   page says to let the pasta cool for a count of ten first. This is a
   description of the other variant, not a step of the procedure the
   answer gives.

No prompt, case or code was changed after this review (stage 6 changes
no pipeline behavior). Notes 1–3 are recorded as known issues in the
README.
