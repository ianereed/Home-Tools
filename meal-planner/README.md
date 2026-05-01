# meal-planner

Two-part meal planning system: a Google Apps Script frontend (the planning UI lives in a Google Sheet) plus a Python sidecar that does heavy LLM work via the Gemini API for recipe categorization and pantry consolidation.

## What it is

```
  Google Sheet (Apps Script UI) ◀───────────┐
        │                                    │
        ▼                                    │
  bulk_import.py    (Gemini batch) ─────────▶│
  consolidate.py    (Gemini batch) ─────────▶│
  Photo upload → vision (gemini-2.5-flash) ──┘
```

Apps Script side handles the daily UX. Python side runs locally on demand for the heavy batches.

## Audience

You + family. Lives in your **personal** Google account, not Antora's.

## Status

**Fully working**. Gemini API integration stable.

## ⚠️ Critical model rules

These are the most easily-broken things in the project (memory: `project_meal_planner.md`):

| Task | Model | Why |
|------|-------|-----|
| Recipe categorization | `gemini-2.5-flash-lite` | RPD (requests-per-day) headroom; handles batch volume |
| Pantry consolidation | `gemini-2.5-flash-lite` | Same |
| Bulk recipe import | `gemini-2.5-flash-lite` | Same |
| Single-photo recipe vision | `gemini-2.5-flash` | Vision quality requires the larger model |
| ❌ ANY task | `gemini-1.5-flash` | DOES NOT WORK — don't use |

If the categorization step starts failing, FIRST check that `gemini-2.5-flash-lite` is still the model. Tinkering with model selection has bitten this project before.

## Layout

- `apps-script/` — Google Apps Script source. Has its own [`SETUP.md`](apps-script/SETUP.md) for the Sheet-side configuration.
- `bulk_import.py` (415 lines) — bulk recipe ingestion via Gemini
- `consolidate.py` — pantry / grocery list consolidation

## Setup

```bash
cd meal-planner
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # add GEMINI_API_KEY
```

## Future

- Pantry-aware suggestions (use what's about to expire)
- Better leftover handling
- Possibly Apple Shortcuts integration for "what's for dinner" voice query

## Out of scope

- Calorie tracking (Apple Health does this)
- Multi-week prep automation (manual planning is faster than automating it)
- Shopping integration (out of scope for this hobby project)

## Reference

- Memory: `project_meal_planner.md`
- `apps-script/SETUP.md` — Sheet/Apps Script setup
