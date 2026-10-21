"""The one daily resting-HR series every page and calculation reads.

Several devices can report a resting HR for the same day (Garmin's daily
value, Apple Health's RestingHeartRate, Suunto historically). Averaging them
blends different algorithms, so each day takes the single best source instead:
Garmin, then Suunto, then Apple. Only rows tagged context='resting' count;
intraday samples (context='intraday') are never a resting value.
"""
from __future__ import annotations

import sqlite3

SOURCE_PREFERENCE = ("garmin", "suunto", "apple")

_RANK = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(SOURCE_PREFERENCE))

# One row per day: (date 'YYYY-MM-DD', rhr bpm), oldest first. Takes one
# parameter, the first date to include as 'YYYY-MM-DD' ('' for all history).
DAILY_RESTING_HR_SQL = f"""
    SELECT date, rhr FROM (
        SELECT substr(timestamp, 1, 10) AS date, ROUND(AVG(bpm)) AS rhr,
               ROW_NUMBER() OVER (
                   PARTITION BY substr(timestamp, 1, 10)
                   ORDER BY CASE source {_RANK} ELSE {len(SOURCE_PREFERENCE)} END
               ) AS rn
        FROM heart_rate
        WHERE context = 'resting' AND timestamp >= ?
        GROUP BY substr(timestamp, 1, 10), source
    )
    WHERE rn = 1
    ORDER BY date
"""


def daily_resting_hr(conn: sqlite3.Connection, since: str = "") -> dict[str, float]:
    """{date: resting bpm} from `since` (a 'YYYY-MM-DD' date, '' for all), oldest first."""
    return {d: v for d, v in conn.execute(DAILY_RESTING_HR_SQL, (since,)) if v is not None}
