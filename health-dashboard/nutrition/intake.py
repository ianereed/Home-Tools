"""Logged food intake by day, merging sources by precedence.

MyFitnessPal is the food log Ian uses; the Apple Health receiver and the Garmin Connect+
food log are fallbacks; Garmin's consumed-calories (what a linked food app pushes to
Garmin) is calories-only, used only when nothing better exists for that day.
"""
from __future__ import annotations

SOURCE_PRECEDENCE = ("mfp", "apple", "garmin", "manual")
COLS = ("calories_kcal", "protein_g", "carbs_g", "fat_g", "saturated_fat_g", "fiber_g", "sugar_g",
        "sodium_mg", "potassium_mg", "cholesterol_mg")


def intake_by_date(conn, start: str, end: str) -> dict[str, dict]:
    """{date: {col: value, 'source': ..., 'updated_at': ...}} for days with any logged intake."""
    rank = {s: i for i, s in enumerate(SOURCE_PRECEDENCE)}
    out: dict[str, dict] = {}
    rows = conn.execute(
        f"SELECT date, source, updated_at, {', '.join(COLS)} FROM nutrition_daily WHERE date BETWEEN ? AND ?",
        (start, end)).fetchall()
    for r in rows:
        ds, src, upd = r[0], r[1], r[2]
        cur = out.get(ds)
        if cur is not None and rank.get(src, 99) >= rank.get(cur["source"], 99):
            continue
        out[ds] = {"source": src, "updated_at": upd, **dict(zip(COLS, r[3:]))}
    try:
        energy = conn.execute(
            "SELECT date, consumed_kcal FROM daily_energy WHERE date BETWEEN ? AND ? AND consumed_kcal IS NOT NULL",
            (start, end)).fetchall()
    except Exception:  # pre-migration DB
        energy = []
    for ds, kcal in energy:
        if ds not in out:
            out[ds] = {"source": "garmin_consumed", "updated_at": None, **{c: None for c in COLS},
                       "calories_kcal": kcal}
    return out
