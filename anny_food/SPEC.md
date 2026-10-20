# Anny Food — Pregnancy Nutrition Tracker (revised spec v2)

## Context

Ian wants a new console tab, **Anny Food**, next to Recipes on the Mac mini (`http://homeserver:8503/?tab=anny`). It is only for Anny and is completely separate from Ian's Garmin/MFP/health-dashboard work. Its job is to track Anny's weekly food-group intake against DGA 2020–2025 Table 5-1 pregnancy targets, scaled by trimester (she is in T1 now). It reuses the shared recipe library.

This spec replaces the pasted prompt. It is grounded in what the repo actually contains and in three decisions Ian made while we workshopped it:

1. **No runtime LLM.** The repo keeps a $0, no-Anthropic budget (`Mac-mini/PHASE15.md:7`), so a Claude Code session computes the recipe breakdowns and imports them. The app never calls an API.
2. **v1 = breakdown engine + Track mode.** Plan mode (the CP-SAT solver, suggest-recipe and shopping list) becomes a separate v2. The schema and config stay planner-ready.
3. **Current trimester is T1.** At T1 the scale factor is 1.000, so the open questions about scaling only bite from week 14.

## What the repo actually has (replaces the prompt's `[[...]]` blanks)

- **Stack.** The console is Streamlit, running from `console/app.py` on port 8503, bound to the mini's Tailscale IP.
  - Tabs are registered in three places in `app.py`: the import at line 26, `_TAB_ORDER` at line 35, and the `_TAB_LABELS`/`_TAB_RENDERERS` dicts at lines 36–51.
  - Recipes is `console/tabs/plan.py`, `render()`, under the tab key `recipes`.
  - `st.tabs` renders **every** tab on every rerun, so the new tab's render must be cheap and wrapped in try/except, like `plan.py:75-83`.
- **Recipes database.** SQLite at `~/Home-Tools/meal_planner/recipes.db` in WAL mode (`meal_planner/db.py:7-15`).
  - `recipes(id, title, base_servings, …)`.
  - `ingredients(recipe_id, name, qty_per_serving REAL, qty_raw, unit, notes, …)`. Ingredients are structured rows, already **per serving**. About 46 of them have a null qty.
  - Migrations are `CREATE TABLE IF NOT EXISTS` plus `_add_column_if_missing` in `init_db()`.
  - Reads go through `meal_planner/queries.py`: `search_recipes`, `get_recipe`, `list_ingredients`.
- **Library size.** Roughly 40–80 recipes, and only the mini has the real data (the local copy is empty). They are mostly dinners, plus a few of Anny's empty stub recipes (ids 75–79). `meal_planner/BP recipes/` holds 32 DASH breakfast/lunch/dinner markdown files, which are mostly *not* in the database.
- **Tests.** pytest from the repo root: `python -m pytest console/tests meal_planner/tests -q`. The last run was 433 passing.
  - Tests use a temp database via `init_db(tmp_path/"recipes.db")` plus `monkeypatch.setattr(_db, "DB_PATH", …)`.
  - Streamlit is faked with `_FakeSt` (`console/tests/test_plan_autosave_grid_rerun.py:43-104`).
  - Pure helpers live in modules that don't import Streamlit (the pattern of `console/tabs/_recipe_form.py`).
- **Deploy.** Merge the PR, then `ssh homeserver 'cd ~/Home-Tools && git pull --ff-only'`, then `launchctl kickstart -kp gui/$(id -u)/com.home-tools.console`.
  - The mini has an **uncommitted local edit to `console/run.sh`**, so do not touch `run.sh`.
  - Add **no new dependencies**: use stdlib `tomllib` for config instead of YAML, and no `anthropic` SDK.
- **Users.** The console has no user concept. The `app.py:4-5` docstring calls it "NOT a joint surface for Anny"; update that line. Anny already reaches the console over Tailscale, because she uses Recipes.
- **Separation.** health-dashboard has its own `health.db` and never touches `recipes.db`. The new tables go in `recipes.db` alongside recipes and never read from health-dashboard.

## Fixes to the original prompt

| Prompt said | Change | Why |
|---|---|---|
| "me and my partner", `who` column | Anny only; no `who` | Ian's requirement |
| Anthropic API module, retries, cost log, dry-run, `claude-sonnet-4-5` | Claude Code computes breakdowns offline → JSON → `import` CLI | $0 policy; Ian's choice |
| Ad-hoc entries parsed by LLM | **Staples list** of about 80 pre-computed simple foods, plus a manual custom-entry form, plus a "needs breakdown" queue | No runtime LLM. The library is mostly dinners, so breakfasts and snacks need staples anyway |
| Hash of ingredients text | Hash of ingredient rows (name, qty_per_serving, qty_raw, unit, notes) **plus base_servings** | Ingredients are structured rows; a change in servings must invalidate the breakdown |
| Create/edit hook queues recompute | No hook. Staleness is derived by comparing hashes at read time; the tab shows a "breakdown pending" badge | Zero changes to `plan.py`; nothing to queue without a runtime LLM |
| Legumes go to "whichever target is further behind" | Deterministic: legumes count toward `veg_legumes` until its weekly target is met, then overflow to protein at **4 oz eq per cup eq** | Same intent, but explainable and stable day to day. The prompt never stated the cup→oz conversion |
| Refined grains as a min target (and scaled to 3.8/4.0) | `grain_refined` becomes a **limit** row (max 3 oz eq/day, held at baseline); `grain_whole` is the min target | DGA lists refined as "≤". Ian's own workbook says refined is held at baseline, but the prompt table scales it. Confirm against the xlsx before T2 |
| Weeks with start date + "New week" archive button | Weeks are derived from entry dates (Monday-start, configurable); history is a computed view | No archive state to manage; retroactive weeks come free |
| YAML config | `targets.toml` (stdlib `tomllib`) | No new dependency in the console venv |
| Overrides stored on the breakdown row | Separate `anny_nutrition_override(recipe_id, key, value, set_at, ingredients_hash_at_set)` | Recompute can never clobber an override; the UI warns when the ingredients changed after an override was set |
| Safety flags list | Add liver/high-retinol, refrigerated smoked seafood, unpasteurized juice/cider, and raw-milk products to the existing list | Standard listeria and teratogen guidance |
| Albacore cap 6 oz/wk | Keep 6 (from Ian's DGA source) as a config value, with a note that FDA's current chart says about 1 serving (4 oz)/wk | Easy to tighten later |
| Dairy | Count calcium-fortified soy milk/yogurt as dairy; other plant milks don't count | DGA definition |
| Plan mode in phases 4–5 | Moved to v2, separate spec | Ian's choice |

## Architecture

New pure-Python package **`anny_food/`** at the repo root. It contains no Streamlit and is unit-testable.

- `anny_food/targets.toml`: one row per key with `label, unit, period (day|week), kind (min|limit|ceiling), t1, t2, t3, derived_from`.
  - It includes `veg_total` and `protein_total` as derived rows, a seafood ceiling of 12, an albacore cap of 6, and `grain_refined` as a limit.
  - The sodium, added sugar, caffeine, fluids, inositol and other-kcal rows from the workbook go in as `enabled = false` limit rows, ready for later.
  - It also holds the trimester scale factors (1.000 / 1.170 / 1.226) and the workbook's scaling notes.
- `anny_food/rules.md`: the equivalence rules from the prompt, with the fixes above. A Claude Code session reads it when computing breakdowns.
- `anny_food/db.py`: `init_anny_tables(conn)`, using the `meal_planner/db.py` style (`CREATE TABLE IF NOT EXISTS`). Tables:
  - `anny_recipe_nutrition(recipe_id PK→recipes CASCADE, ingredients_hash, values_json, breakdown_json, safety_flags_json, confidence, notes, model, created_at)`
  - `anny_nutrition_override(...)` (above)
  - `anny_staples(id, name, serving_label, values_json, safety_flags_json, notes)`
  - `anny_log(id, eaten_on DATE, logged_at, kind recipe|staple|custom|pending, recipe_id, staple_id, servings, label, values_json NULL, note)`. A `pending` row holds Anny's free text until the next Claude Code pass fills in `values_json`.
  - `anny_settings(key, value)`: due_date, trimester_override, week_start.
- `anny_food/breakdown.py`:
  - `ingredients_hash(recipe, ingredients)`.
  - `validate(payload)`, a hand-rolled schema check: every subgroup key present, numbers ≥ 0, confidence in {low, med, high}.
  - `effective_values(recipe_id)`, which applies overrides on top of the computed values.
  - `pending(conn)` returns the recipes that are missing or stale, plus `pending` log rows.
- `anny_food/track.py` (pure functions):
  - `trimester(due_date, today)`
  - `week_bounds(date, week_start)`
  - `weekly_totals(entries)` with legume allocation and derived totals
  - `pace(day_index)`
  - `status(value, target, kind)` returning under / on-pace / met / over-ceiling
  - `daily_subtotals`
  - `history(weeks)` giving % of target met
- `anny_food/cli.py`, run as `python -m anny_food.cli`:
  - `export-pending [--ids …] [--force]`: JSON to stdout with each recipe's ingredients, base_servings and hash, plus pending log rows.
  - `import <file|->`: validates, then upserts and refuses mismatched hashes. Also supports `--dry-run`.
  - `seed-staples <file>`
  - `show <id>`: prints the audit table.
- **Claude Code workflow:** a project command, `.claude/commands/anny-food-breakdowns.md`, runs:
  1. ssh to the mini and run `export-pending`.
  2. Read `rules.md`.
  3. Compute each breakdown, with a per-ingredient `breakdown[]` array.
  4. Show Ian an audit table.
  5. Pipe the JSON to `import` on the mini.

  This replaces the prompt's backfill CLI and LLM module.
- **UI:**
  - `console/tabs/anny_food.py`: `render()` wrapped in try/except.
  - `console/tabs/_anny_food_view.py`: pure helpers for formatting and bar HTML.
  - Register it after `recipes` in `_TAB_ORDER`. Label: `":pregnant_woman: Anny Food"`. Key: `anny`.

## Track mode UI (v1)

- A not-medical-advice banner at the top (DGA 2020–2025 pattern; confirm with an OB or dietitian). Below it: the trimester and gestational week, computed from the due date in settings.
- **Progress bars** are a small custom HTML/CSS helper, because `st.progress` has no pace marker or colors. Each bar shows:
  - weekly cumulative vs target
  - a pace tick (day n/7)
  - a color for status: under / on-pace / met / over-ceiling, plus limit rows over max
  - single-column, large-type layout on phones

  Subgroups are nested under the derived totals.
- **Quick-add:** one search box covering recipes and staples. Then choose servings (default 1, step 0.25), set the date (default today), and tap add.
  - "Custom" opens number inputs per group.
  - "Don't know": the free text is saved as `pending` and shown with a badge.
- **Entries** are listed by day in expanders, with per-day subtotals. Each entry can be edited or deleted.
- **Recipe card** (on expand): per-serving values, the audit `breakdown[]` table, safety flags, confidence, and **Edit breakdown** (overrides), with a stale-override warning.
- **History:** a table of past weeks with % met per row, derived from `anny_log`.
- Anny gets a home-screen link to `homeserver:8503/?tab=anny`, which the existing deep-link rotation already supports.

## Phases (stop for Ian's review after each)

1. **Spec + schema.**
   - Save this spec as `anny_food/SPEC.md`.
   - Write `targets.toml`, `rules.md`, `anny_food/db.py` and the breakdown, validation and hash code, `cli.py`, and tests.
   - Ian provides `Pregnancy Diet Planning.xlsx` (dropped in the repo, gitignored if needed), and I verify `targets.toml` against it, especially how the grains are scaled.
2. **Breakdowns for 10 recipes + staples.**
   - Add the `.claude/commands/anny-food-breakdowns.md` workflow.
   - Compute 10 recipes that Ian or Anny pick, plus about 80 staples (dairy, fruit, eggs, nuts, breads, cereals, yogurt, cheeses, common snacks).
   - Show them in a side-by-side audit table. Import only after Ian approves.
3. **Track mode UI.** Register the tab, then build the bars, quick-add, entries, overrides and history, with `_FakeSt` tests. Deploy to the mini, then QA on a phone viewport (`/qa`).
4. **Full library + polish.**
   - Compute the remaining recipes and clear the pending queue.
   - Write `docs/anny-food.md` (targets, rules, audit/override, the breakdown workflow).
   - Do a mobile pass (`/design-review`) and update the `app.py` docstring.
5. **(v2, separate spec) Plan mode.** Solver behind an interface, grid, gaps, shopping list (reusing `runner.send_recipes_to_todoist_sync`), lock → track. Could also import the BP/DASH breakfast and lunch recipes into the library to give the planner more to choose from.

## Tests

`anny_food/tests/`, run with `python -m pytest console/tests meal_planner/tests anny_food/tests -q`:

- **Pure math:** trimester boundaries (13/14, 27/28), week bounds, pace, status colors, the legume overflow (veg first, then ×4 into protein), derived totals, limit and ceiling rows, overrides winning over computed values.
- **Import:** schema rejection, hash-mismatch refusal, stale detection after an ingredient edit or a base_servings change.
- **Golden fixtures:** 6 hand-computed recipes ("2 eggs + 1 cup spinach" → protein_meat_eggs 2.0, veg_dark_green 0.5, choline ≈ 300 mg). Claude Code's JSON for these is checked in, and the test asserts it is within ±25%, guarding the rules against regressions on recompute.
- **UI:** render smoke via `_FakeSt`, quick-add creating the right `anny_log` row.

## Verification

- `pytest` is green locally (433 tests plus the new ones).
- On the mini:
  1. `python -m anny_food.cli export-pending | head` lists the recipes.
  2. `import --dry-run` on the 10 audited breakdowns is clean.
  3. `import` runs.
  4. `show <id>` matches the audit.
- In the browser at `http://homeserver:8503/?tab=anny`, at both a 375px viewport and on desktop:
  1. Add a recipe, a staple, a custom entry and a pending entry.
  2. Check the bars and the pace tick against a hand calculation for one row.
  3. Edit and delete an entry.
  4. Set an override and confirm a recompute doesn't clobber it.
  5. Confirm the Recipes tab is unaffected.

## Source workbook context (from Ian's summary of `Pregnancy Diet Planning.xlsx`)

- **Calories.** Baseline is 2,000 kcal, plus +0 / +340 / +452 kcal per trimester, giving scale factors of 1.000 / 1.170 / 1.226.
- **What gets scaled.** Veg (total and its subgroups), fruit and protein (total and its subgroups) are multiplied by the scale factor.
  - Whole grains use 1.5× the factor's increment, which matches the 3.8/4.0 figures.
  - Dairy, oils and the other-uses kcal are held at baseline. The workbook says refined grains are held too, but the prompt's table contradicts that; verify.
  - Values are rounded to 0.1.
- **Extra rows.** Calcium 1000 mg, choline 450 mg, inositol 500 mg (unconfirmed), fluids 8 cups. Limits: added sugar 50 g, sodium 2300 mg, caffeine 300 mg.
- **Seafood.** 8–12 oz/wk, low-mercury choices only.
- **Safety notes** are the source of the flags. White tuna is limited to 6 oz/wk.
- **Practical reference servings.** Anny's own ("½ box cut fruit", "card-deck meat", "1.5 servings hot oats + 1 slice bread") should become staples or helper text in the UI.
- **Primary source.** DGA 2020–2025 Table 5-1 plus the equivalence footnotes on pp. 145–146. The 2025–2030 DGA largely mirrors it for pregnancy.
- **Disclaimer.** Not medical advice; confirm with an OB or dietitian.
