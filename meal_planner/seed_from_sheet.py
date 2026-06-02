"""
Seed recipes.db from the existing Google Sheet (one-time migration).

Run on the mini:
    cd ~/Home-Tools
    python -m meal_planner.seed_from_sheet

Required env vars (set in meal_planner/.env or environment):
    MEAL_PLANNER_SHEET_ID         — Google Sheet ID (from the URL)
    GOOGLE_SERVICE_ACCOUNT_PATH   — path to service-account JSON file
    GEMINI_API_KEY                — Gemini API key

Optional env vars:
    TODOIST_SECTIONS              — JSON map of section names to IDs
                                    (section names used as Gemini hints)
    SEED_DELAY                    — seconds between Gemini calls (default: 3)

One-time Google setup (if not done):
    1. Create a service account in Google Cloud Console (or reuse one).
    2. Enable the Google Sheets API for the project.
    3. Download the JSON key file and set GOOGLE_SERVICE_ACCOUNT_PATH.
    4. Share the Sheet with the service account email (Viewer access).

Sheet format expected:
    Row 1, col 1+: recipe names (one per column)
    Row 2+, same col: ingredient strings ("2 tbsp soy sauce", ...)
    Empty cell = end of that recipe's ingredient list
    Tab name = tag applied to all recipes in that tab (lowercased)
    "readme" tab is skipped.

Resumability:
    Progress is persisted in ~/Home-Tools/meal_planner/seed_progress.json.
    Re-running skips already-seeded (sheet_tab, col_index) pairs so no
    Gemini quota is wasted and no duplicate recipes are inserted.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from meal_planner import db as _db
from meal_planner.db import add_recipe_tag, init_db, insert_recipe
from meal_planner.qty_parse import parse_qty
from meal_planner.sections import CANONICAL_SECTIONS, GROCERY_SECTIONS, classify

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash-lite:generateContent"
)

# Real Todoist grocery sections, used as the Gemini hint when TODOIST_SECTIONS
# isn't in the environment. (Previously fake names like "Produce"/"Pantry" that
# don't exist in Todoist, which is how "Pantry"-tagged ingredients ended up with
# no real section.)
_DEFAULT_SECTIONS = list(GROCERY_SECTIONS)

_INGREDIENT_PROMPT_TEMPLATE = """\
Parse these ingredient strings for the recipe "{title}" (base: {base_servings} servings).

Each ingredient string gives the TOTAL quantity for the full recipe.
Todoist grocery sections available: {section_names}

Return a JSON array with one object per ingredient line, in the same order:
[
  {{"name": "soy sauce", "qty": 2.0, "unit": "tbsp", "notes": "", "todoist_section": "Pantry"}},
  ...
]

Rules:
- name: ingredient name only (no quantity, no unit in this field)
- qty: numeric total quantity as written; null if uncountable or amount is vague (e.g. "to taste", "a pinch")
- unit: unit string, "" if none
- notes: extra preparation text like "minced", "skinless", "to taste"; "" if none
- todoist_section: assign to one of the provided sections; use "{fallback}" if unsure

Ingredient strings (one per line):
{ingredient_lines}

Return ONLY the JSON array, no other text."""


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _load_env() -> None:
    script_dir = Path(__file__).parent
    for d in [script_dir, script_dir.parent]:
        env_file = d / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            return
    load_dotenv()


def _require(key: str) -> str:
    val = os.environ.get(key, "").strip()
    if not val:
        print(f"Error: required env var '{key}' is not set. Add it to meal_planner/.env.", file=sys.stderr)
        sys.exit(1)
    return val


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


# ---------------------------------------------------------------------------
# Progress sidecar
# ---------------------------------------------------------------------------

_PROGRESS_PATH = _db.DB_DIR / "seed_progress.json"


def _load_progress(path: Path | None = None) -> set[str]:
    p = path or _PROGRESS_PATH
    if p.exists():
        data = json.loads(p.read_text())
        return set(data.get("done", []))
    return set()


def _save_progress(done: set[str], path: Path | None = None) -> None:
    p = path or _PROGRESS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"done": sorted(done)}, indent=2))


def _progress_key(sheet_name: str, col_index: int) -> str:
    return f"{sheet_name}::{col_index}"


# ---------------------------------------------------------------------------
# Gemini ingredient parser
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, api_key: str) -> str | None:
    """Call Gemini, retry on 429/503. Returns response text or None on failure."""
    for attempt in range(4):
        resp = requests.post(
            GEMINI_ENDPOINT,
            params={"key": api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        if resp.status_code not in (429, 503):
            break
        body = resp.json()
        retry_delay = 60
        for detail in body.get("error", {}).get("details", []):
            if detail.get("@type", "").endswith("RetryInfo"):
                delay_str = detail.get("retryDelay", "60s")
                retry_delay = int(re.sub(r"[^0-9]", "", delay_str) or "60") + 2
                break
        print(f"  Rate limited — waiting {retry_delay}s…", flush=True)
        time.sleep(retry_delay)

    if resp.status_code != 200:
        print(f"  Gemini HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return None

    try:
        return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        print(f"  Unexpected Gemini response shape: {exc}", file=sys.stderr)
        return None


def _parse_ingredients(
    title: str,
    base_servings: int,
    ingredient_strings: list[str],
    section_names: list[str],
    api_key: str,
) -> list[dict] | None:
    """Return list of parsed ingredient dicts or None on any failure."""
    fallback = section_names[0] if section_names else "Other"
    prompt = _INGREDIENT_PROMPT_TEMPLATE.format(
        title=title,
        base_servings=base_servings,
        section_names=", ".join(section_names),
        fallback=fallback,
        ingredient_lines="\n".join(ingredient_strings),
    )
    text = _call_gemini(prompt, api_key)
    if text is None:
        return None

    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        print(f"  Could not find JSON array in Gemini response for '{title}'", file=sys.stderr)
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        print(f"  JSON parse error for '{title}': {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Sheet reader
# ---------------------------------------------------------------------------

def _open_sheet(sheet_id: str, service_account_path: str):
    """Return a gspread Spreadsheet object."""
    try:
        import gspread
    except ImportError:
        print(
            "Error: 'gspread' is not installed. Run: pip install gspread",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        gc = gspread.service_account(filename=service_account_path)
    except Exception as exc:
        print(f"Error opening service account '{service_account_path}': {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        return gc.open_by_key(sheet_id)
    except Exception as exc:
        print(f"Error opening sheet '{sheet_id}': {exc}", file=sys.stderr)
        sys.exit(1)


def _get_recipes_from_worksheet(ws) -> list[tuple[str, int, list[str]]]:
    """Return list of (title, col_index_0based, ingredient_strings) for a worksheet."""
    all_values = ws.get_all_values()
    if not all_values:
        return []

    header_row = all_values[0]
    data_rows = all_values[1:]
    recipes = []

    for col_idx, title in enumerate(header_row):
        title = title.strip()
        if not title:
            continue
        ingredients = []
        truncated_at: int | None = None
        for row_idx, row in enumerate(data_rows):
            if col_idx >= len(row):
                break
            cell = row[col_idx].strip()
            if not cell:
                truncated_at = row_idx
                break
            ingredients.append(cell)
        if truncated_at is not None:
            dropped = sum(
                1 for row in data_rows[truncated_at + 1:]
                if col_idx < len(row) and row[col_idx].strip()
            )
            if dropped > 0:
                print(
                    f"WARN: {title!r} truncated at row {truncated_at + 2};"
                    f" {dropped} non-empty cell(s) below dropped",
                    file=sys.stderr,
                )
        recipes.append((title, col_idx, ingredients))

    return recipes


# ---------------------------------------------------------------------------
# Main seed logic
# ---------------------------------------------------------------------------

def seed(
    sheet_id: str,
    service_account_path: str,
    api_key: str,
    section_names: list[str],
    delay: float = 3.0,
    db_path: Path | None = None,
    progress_path: Path | None = None,
) -> tuple[int, int]:
    """Seed recipes.db from Google Sheet. Returns (seeded_count, skipped_count)."""
    p_db = db_path or _db.DB_PATH
    p_progress = progress_path or _PROGRESS_PATH

    init_db(p_db)
    done = _load_progress(p_progress)

    spreadsheet = _open_sheet(sheet_id, service_account_path)
    worksheet_data = [
        (ws, _get_recipes_from_worksheet(ws))
        for ws in spreadsheet.worksheets()
        if ws.title.lower() != "readme"
    ]
    total_recipes = sum(len(rs) for _, rs in worksheet_data)

    seeded = 0
    skipped = 0
    recipe_num = 0

    for ws, recipes_in_tab in worksheet_data:
        tab_name = ws.title
        tag = tab_name.lower()  # tab name is the tag (normalized already by add_recipe_tag)

        for title, col_idx, ingredient_strings in recipes_in_tab:
            recipe_num += 1
            key = _progress_key(tab_name, col_idx)
            prefix = f"[{recipe_num}/{total_recipes}] sheet={tab_name!r} col={col_idx}"

            if key in done:
                print(f"{prefix} title=<already seeded> — skipping")
                skipped += 1
                continue

            base_servings = 4

            if not ingredient_strings:
                print(f"{prefix} title={title!r} — no ingredients, skipping")
                skipped += 1
                continue

            print(f"{prefix} title={title!r} ({len(ingredient_strings)} ingredients) … ", end="", flush=True)

            if recipe_num > 1 and delay > 0:
                time.sleep(delay)

            parsed = _parse_ingredients(
                title, base_servings, ingredient_strings, section_names, api_key
            )
            if parsed is None:
                print("PARSE FAILED — skipping recipe")
                skipped += 1
                continue

            conn = _db._get_conn(p_db)
            try:
                recipe_id = insert_recipe(
                    title=title,
                    base_servings=base_servings,
                    path=p_db,
                    conn=conn,
                )
                add_recipe_tag(recipe_id, tag, path=p_db, conn=conn)
                ing_count, ing_warnings = _insert_ingredients_batch(
                    recipe_id=recipe_id,
                    parsed=parsed,
                    base_servings=base_servings,
                    path=p_db,
                    conn=conn,
                )
                conn.commit()
            except Exception as exc:
                print(f"  recipe failed: {exc} — skipping")
                skipped += 1
                continue
            finally:
                conn.close()

            done.add(key)
            _save_progress(done, p_progress)
            print(f"recipe_id={recipe_id}, ingredients={ing_count}")
            for w in ing_warnings:
                print(f"    WARNING: {w}")
            seeded += 1

    return seeded, skipped


def _insert_ingredients_batch(
    *,
    recipe_id: int,
    parsed: list[dict],
    base_servings: int,
    path: Path,
    conn: sqlite3.Connection | None = None,
) -> tuple[int, list[str]]:
    """Insert all parsed ingredients for a recipe. Returns (count_inserted, warnings).

    Inserts every row whose `name` is non-empty. Never silently drops a row.
    warnings: one entry per unparseable qty, formatted as
      f"row {sort_order}: {name!r} qty={qty_raw!r} not parseable, stored verbatim"

    When conn is passed, uses it without committing or closing (caller owns
    the transaction). When conn is None, opens, commits, and closes its own.
    """
    owned = conn is None
    if owned:
        p = path or _db.DB_PATH
        conn = _db._get_conn(p)
    try:
        count = 0
        warnings: list[str] = []
        for sort_order, item in enumerate(parsed):
            name = str(item.get("name", "")).strip()
            if not name:
                continue

            raw_q = item.get("qty")
            qty_per_serving: float | None
            qty_raw_str: str | None

            if raw_q is None:
                qty_per_serving = None
                qty_raw_str = None
            elif isinstance(raw_q, bool):
                qty_per_serving = None
                qty_raw_str = repr(raw_q)
                warnings.append(
                    f"row {sort_order}: {name!r} qty={raw_q!r} not parseable, stored verbatim"
                )
            elif isinstance(raw_q, (int, float)):
                qty_per_serving = float(raw_q) / base_servings
                qty_raw_str = str(raw_q)
            elif isinstance(raw_q, str):
                numeric, normalized = parse_qty(raw_q)
                if numeric is not None:
                    qty_per_serving = numeric / base_servings
                    qty_raw_str = normalized
                elif normalized:
                    qty_per_serving = None
                    qty_raw_str = normalized
                    warnings.append(
                        f"row {sort_order}: {name!r} qty={raw_q!r} not parseable, stored verbatim"
                    )
                else:
                    qty_per_serving = None
                    qty_raw_str = None
            else:
                qty_per_serving = None
                qty_raw_str = repr(raw_q)
                warnings.append(
                    f"row {sort_order}: {name!r} qty={raw_q!r} not parseable, stored verbatim"
                )

            unit = str(item.get("unit", "") or "").strip() or None
            notes = str(item.get("notes", "") or "").strip() or None
            todoist_section = str(item.get("todoist_section", "") or "").strip() or None
            # Photo-intake/corpus ingredients arrive with no section. Rather than
            # leave it NULL (which dumps to the produce fallback at send time),
            # classify deterministically. Also a safety net for any sheet item the
            # Gemini categorizer left blank or set to a non-canonical name.
            if todoist_section not in CANONICAL_SECTIONS:
                todoist_section = classify(name, notes or "")

            cur = conn.execute(
                """
                INSERT INTO ingredients
                  (recipe_id, name, qty_per_serving, qty_raw, unit, notes,
                   todoist_section, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (recipe_id, name, qty_per_serving, qty_raw_str, unit, notes,
                 todoist_section, sort_order),
            )
            if cur.rowcount == 1:
                count += 1

        if owned:
            conn.commit()
        return count, warnings
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    _load_env()

    sheet_id = _require("MEAL_PLANNER_SHEET_ID")
    service_account_path = _require("GOOGLE_SERVICE_ACCOUNT_PATH")
    api_key = _require("GEMINI_API_KEY")

    sections_json = _get("TODOIST_SECTIONS", "")
    if sections_json:
        try:
            section_names = list(json.loads(sections_json).keys())
        except json.JSONDecodeError:
            print("Warning: TODOIST_SECTIONS is not valid JSON — using defaults", file=sys.stderr)
            section_names = _DEFAULT_SECTIONS
    else:
        section_names = _DEFAULT_SECTIONS

    delay = float(_get("SEED_DELAY", "3"))

    print(f"Seeding recipes.db from sheet {sheet_id[:8]}… (sections: {', '.join(section_names)})")
    print(f"DB path: {_db.DB_PATH}")
    print(f"Progress: {_PROGRESS_PATH}")
    print()

    seeded, skipped = seed(
        sheet_id=sheet_id,
        service_account_path=service_account_path,
        api_key=api_key,
        section_names=section_names,
        delay=delay,
        db_path=None,
        progress_path=None,
    )

    print()
    print(f"Done. Seeded: {seeded}, Skipped/failed: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
