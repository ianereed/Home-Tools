# meal_planner/eval — Recipe-photo LLM bake-off

Phase 15 bake-off: compare vision models on recipe-photo extraction accuracy,
latency, and quota headroom. Output: `MODEL_CHOICE.md`.

## Corpus layout

```
recipe_photos/
  <basename>.png   (or .jpg / .jpeg)
  <basename>.golden.json
```

Each photo has a sibling golden JSON file with the expected extraction output.
Golden files follow the same schema as the prompt output (see below).
Photos and golden files are gitignored; only the `.gitkeep` placeholder commits.

## Golden JSON schema

```json
{
  "title": "Recipe Title",
  "ingredients": [
    {"qty": "2", "unit": "cup", "name": "all-purpose flour"},
    {"qty": "1", "unit": "tsp", "name": "salt"},
    {"qty": null, "unit": null, "name": "pepper to taste"}
  ],
  "tags": ["baking", "vegetarian"],
  "instructions": "1. Preheat oven to 350°F.\n2. Mix dry ingredients in a large bowl.\n3. ..."
}
```

- `qty` — quantity as string, or `null` for "to taste", "as needed", etc.
- `unit` — unit string, `null` or `""` if no unit
- `name` — ingredient name only, no qty/unit embedded
- `instructions` — preparation steps as a string (numbered steps joined by
  `\n`), or `null` if the image shows no instructions (ingredient-only
  card). Added in Phase 19 (2026-05). See `_PHASE19_NOTES.md` for
  per-image transcription notes.

See `recipe_extraction_prompt.txt` for the canonical prompt sent to all models.

## Synonym normalization

`synonyms.yml` defines canonical-first synonym groups (e.g. `scallion; green onion; spring onion`).
Ingredient names are normalized before F1 scoring so models that echo source phrasing
aren't penalized vs. models that use canonical names.

`unicode_fractions` key maps common Unicode fraction characters to float values for
qty parsing (e.g. `"¼": 0.25`).

## Supported providers

| Provider string | Example | Notes |
|---|---|---|
| `ollama:<tag>` | `ollama:qwen2.5-vl:7b` | Ollama on mini; model must be pulled before bench |
| `gemini-<variant>` | `gemini-2.5-flash` | Free tier; gated by `--gemini-max-calls` |
| `llama-3.2-90b-vision-preview` | (exact string) | Groq free tier; only if `GROQ_API_KEY` in env |

## Run procedure

### Day 0 — smoke run (single photo, all local models)

```bash
python meal_planner/eval/bake_off.py preflight
python meal_planner/eval/bake_off.py run \
  --corpus meal_planner/eval/recipe_photos \
  --corpus-glob "*.png" \
  --models qwen2.5-vl:7b,qwen2.5-vl:3b,llama3.2-vision:11b,minicpm-v:8b \
  --gemini-max-calls 0 \
  --out meal_planner/eval/results/$(date +%Y-%m-%d)/
```

### Day 1 — full local bench + Gemini 6-call smoke

```bash
python meal_planner/eval/bake_off.py run \
  --corpus meal_planner/eval/recipe_photos \
  --models qwen2.5-vl:7b,qwen2.5-vl:3b,llama3.2-vision:11b,minicpm-v:8b,gemini-2.5-flash,gemini-2.5-flash-lite \
  --gemini-max-calls 6 \
  --out meal_planner/eval/results/$(date +%Y-%m-%d)/
```

### Day 2-3 — Gemini full corpus (after RPD reset)

```bash
python meal_planner/eval/bake_off.py run \
  --corpus meal_planner/eval/recipe_photos \
  --models gemini-2.5-flash,gemini-2.5-flash-lite \
  --gemini-max-calls 24 \
  --resume-from latest \
  --out meal_planner/eval/results/$(date +%Y-%m-%d)/
```

### Resume from last run

Use `--resume-from latest` to skip rows already in terminal status
(`parsed_ok`, `parse_fail`, `provider_error`, `budget_exceeded`).

## Output files

- `results/<date>/summary.json` — aggregate scores per model (committed)
- `results/<date>/runs.jsonl` — append-only row per model×photo call (committed)
- `results/<date>/raw/` — raw provider responses (gitignored)

## Kill criteria

See `Mac-mini/PHASE15.md` Section 6 for full kill/pass gate definitions.
A local model passes if: struct_valid ≥ 0.9, F1 ≥ 0.75, p95_s ≤ 30s, peak_rss_gb ≤ 10.

## Decision output

Fill in `MODEL_CHOICE.template.md` and copy to `meal_planner/MODEL_CHOICE.md` once
scores are complete.
