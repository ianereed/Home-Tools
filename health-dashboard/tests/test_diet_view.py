"""Phase 6 smoke tests: render_diet() must not raise, both with an empty
nutrition_daily table (the day-one reality — no tracking app wired up yet)
and with seeded rows (the state Phase 7 will produce). The Diet page carries
no PHI and is never gated, so these tests don't need fake_clinical.
"""
import sqlite3

import pandas as pd

from dashboard import diet_view


def _load_df_factory(db_path):
    def load_df(query, params=()):
        conn = sqlite3.connect(db_path)
        try:
            return pd.read_sql_query(query, conn, params=params)
        finally:
            conn.close()
    return load_df


def test_render_diet_on_empty_db(empty_db):
    diet_view.render_diet(_load_df_factory(empty_db), 90)


def test_render_diet_with_seeded_nutrition(fake_db):
    conn = sqlite3.connect(fake_db)
    rows = [
        ("2026-01-01", 2200, 110, 260, 70, 18, 32, 45, 1900, 3400, "test"),
        ("2026-01-02", 2100, 105, 240, 65, 15, 28, 40, 2100, 3100, "test"),
        ("2026-01-03", 2300, 115, 270, 75, 20, 35, 50, 1700, 3600, "test"),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO nutrition_daily "
        "(date, calories_kcal, protein_g, carbs_g, fat_g, saturated_fat_g, "
        "fiber_g, sugar_g, sodium_mg, potassium_mg, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    diet_view.render_diet(_load_df_factory(fake_db), 90000)
