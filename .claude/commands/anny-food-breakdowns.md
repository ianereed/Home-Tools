Compute Anny Food breakdowns for anything new or changed, then import them on the mini.

1. Export what needs a breakdown (new/edited recipes, and log entries Anny saved as "figure it out later"):
   `ssh homeserver@homeserver 'cd ~/Home-Tools && console/.venv/bin/python -m anny_food.cli export-pending' > pending.json`
   (scratchpad path; `--ids 12 34 --force` to redo specific recipes). If it's empty, say so and stop.
2. Read `anny_food/rules.md` in full, and the `[safety_flags]` table in `anny_food/targets.toml`.
3. For each recipe / log entry, compute per-serving values following rules.md (ingredients are already per serving), with a `breakdown[]` line per (ingredient, key). Copy `recipe_id` and `ingredients_hash` exactly. For many recipes, split into batches across subagents.
4. Show Ian a compact audit table (id | title | main values | confidence | flags) plus judgment calls worth a look.
5. Validate, then import:
   `ssh homeserver@homeserver 'cd ~/Home-Tools && console/.venv/bin/python -m anny_food.cli import - --dry-run' < out.json`
   then the same without `--dry-run`. A hash mismatch means the recipe changed after export: re-export just those ids.
6. Append the imported recipes to `anny_food/data/recipe_breakdowns.json` (replace by recipe_id) so the repo keeps the audit trail, and commit.
