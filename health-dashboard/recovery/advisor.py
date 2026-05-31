"""Informational summaries for the health dashboard.

This module deliberately contains NO training prescriptions ("rest today",
"train hard"). Garmin owns day-to-day training guidance; this dashboard is for
holistic, historical, information-only tracking. We surface *observations*
(what changed, what the trend is) and leave the judgement to the reader.
"""

import sqlite3
from datetime import date, timedelta

from .engine import get_full_recovery_report, get_connection

SLEEP_TARGET_HOURS = 8.0


# --- small query helpers ---------------------------------------------------

def _daily(conn: sqlite3.Connection, sql: str) -> dict[str, float]:
    """Run a (date, value) query into an ordered {date: value} dict."""
    return {r[0]: r[1] for r in conn.execute(sql).fetchall() if r[0] and r[1] is not None}


def _avg_window(daily: dict[str, float], end: date, n: int = 7) -> float | None:
    """Mean of the n days ending at `end` (inclusive). None if no data."""
    lo = (end - timedelta(days=n - 1)).isoformat()
    hi = end.isoformat()
    vals = [v for d, v in daily.items() if lo <= d <= hi]
    return sum(vals) / len(vals) if vals else None


def _direction(current: float | None, prior: float | None, *, higher_is_better: bool,
               threshold: float = 0.03) -> dict | None:
    """Classify a change as up/down/flat with a good/bad read. None if can't compare."""
    if current is None or prior is None or prior == 0:
        return None
    pct = (current - prior) / abs(prior)
    if abs(pct) < threshold:
        direction, good = "flat", None
    else:
        direction = "up" if pct > 0 else "down"
        good = (pct > 0) == higher_is_better
    return {"direction": direction, "good": good, "pct": pct,
            "current": current, "prior": prior}


# --- informational sleep helpers -------------------------------------------

def calculate_sleep_debt(conn: sqlite3.Connection, days: int = 7) -> float:
    """Accumulated shortfall vs an 8h target over the past N nights (capped 14h)."""
    rows = conn.execute(
        """SELECT date, MAX(total_minutes) FROM sleep
           WHERE date >= date('now', ?) GROUP BY date ORDER BY date DESC LIMIT ?""",
        (f"-{days} days", days),
    ).fetchall()
    debt = sum(max(0, SLEEP_TARGET_HOURS - (r[1] or 0) / 60) for r in rows)
    return min(debt, 14.0)


def get_data_timestamps(conn: sqlite3.Connection) -> dict:
    """Most recent data point per source — drives the freshness panel."""
    ts = {}
    ts["sleep"] = (conn.execute("SELECT MAX(date) FROM sleep").fetchone() or [None])[0]
    ts["resting_hr"] = (conn.execute(
        "SELECT MAX(timestamp) FROM heart_rate WHERE context='resting'").fetchone() or [None])[0]
    ts["hrv"] = (conn.execute(
        "SELECT MAX(date) FROM wellness WHERE hrv IS NOT NULL").fetchone() or [None])[0]
    ts["activities"] = (conn.execute("SELECT MAX(date) FROM activities").fetchone() or [None])[0]
    ts["sleep_score"] = (conn.execute(
        "SELECT MAX(date) FROM wellness WHERE sleep_score IS NOT NULL").fetchone() or [None])[0]
    ts["steps"] = (conn.execute(
        "SELECT MAX(date) FROM wellness WHERE steps IS NOT NULL").fetchone() or [None])[0]
    return ts


# --- the Overview homescreen payload ---------------------------------------

def get_overview(since: date | None = None) -> dict:
    """Build the information-only Overview payload.

    `since` is the date of the user's previous visit (for "since you last
    looked"); callers pass None to fall back to a 30-day comparison.
    """
    conn = get_connection()
    try:
        report = get_full_recovery_report()
        today = date.today()
        compare_from = since if since and since < today else (today - timedelta(days=30))

        hrv_daily = _daily(conn, "SELECT date, hrv FROM wellness WHERE hrv IS NOT NULL ORDER BY date")
        steps_daily = _daily(conn, "SELECT date, steps FROM wellness WHERE steps IS NOT NULL ORDER BY date")
        sleep_daily = _daily(conn, "SELECT date, MAX(total_minutes)/60.0 FROM sleep GROUP BY date ORDER BY date")
        rhr_daily = _daily(conn, "SELECT substr(timestamp,1,10) d, AVG(bpm) FROM heart_rate "
                                 "WHERE context='resting' GROUP BY d ORDER BY d")
        ctl_daily = {row["date"]: row["ctl"] for row in report["tsb_history"]}

        def tile(daily, higher_is_better, unit, label, fmt="{:.0f}"):
            series = [v for _, v in sorted(daily.items())][-90:]
            current7 = _avg_window(daily, today, 7)
            prior7 = _avg_window(daily, compare_from, 7)
            latest = daily[max(daily)] if daily else None
            return {
                "label": label, "unit": unit, "fmt": fmt,
                "latest": latest, "avg7": current7, "series": series,
                "change": _direction(current7, prior7, higher_is_better=higher_is_better),
                "higher_is_better": higher_is_better,
            }

        headline = {
            "hrv": tile(hrv_daily, True, "ms", "HRV"),
            "rhr": tile(rhr_daily, False, "bpm", "Resting HR"),
            "sleep": tile(sleep_daily, True, "h", "Sleep", fmt="{:.1f}"),
            "fitness": tile(ctl_daily, True, "CTL", "Fitness", fmt="{:.0f}"),
            "steps": tile(steps_daily, True, "", "Steps", fmt="{:,.0f}"),
        }

        # "Since you last looked" — one observation line per metric that moved.
        since_lines = []
        for key, name, suffix in [("hrv", "HRV", " ms"), ("rhr", "Resting HR", " bpm"),
                                  ("sleep", "Sleep", "h"), ("steps", "Daily steps", ""),
                                  ("fitness", "Fitness (CTL)", "")]:
            ch = headline[key]["change"]
            if not ch or ch["direction"] == "flat":
                continue
            verb = {"up": "up", "down": "down"}[ch["direction"]]
            fmt = headline[key]["fmt"]
            since_lines.append({
                "label": name,
                "detail": f"{verb} from {fmt.format(ch['prior'])}{suffix} to "
                          f"{fmt.format(ch['current'])}{suffix}",
                "good": ch["good"],
            })

        return {
            "compare_from": compare_from.isoformat(),
            "is_first_visit": since is None,
            "freshness": get_data_timestamps(conn),
            "headline": headline,
            "since_lines": since_lines,
            "highlights": _highlights(conn),
            "physio": report["physio"],
            "fitness_curve": report["tsb_history"],
        }
    finally:
        conn.close()


def _highlights(conn: sqlite3.Connection) -> list[dict]:
    """A few notable facts from the last 30 days."""
    out = []
    row = conn.execute(
        "SELECT date, MAX(total_minutes)/60.0 h FROM sleep "
        "WHERE date >= date('now','-30 days') GROUP BY date ORDER BY h DESC LIMIT 1").fetchone()
    if row and row[1]:
        out.append({"label": "Best night (30d)", "value": f"{row[1]:.1f}h", "sub": row[0]})

    row = conn.execute(
        "SELECT date, MAX(total_minutes)/60.0 h FROM sleep "
        "WHERE date >= date('now','-30 days') GROUP BY date ORDER BY h ASC LIMIT 1").fetchone()
    if row and row[1]:
        out.append({"label": "Shortest night (30d)", "value": f"{row[1]:.1f}h", "sub": row[0]})

    row = conn.execute(
        "SELECT strftime('%Y-W%W', date) wk, SUM(duration_minutes)/60.0 hrs "
        "FROM activities WHERE date >= date('now','-90 days') "
        "GROUP BY wk ORDER BY hrs DESC LIMIT 1").fetchone()
    if row and row[1]:
        out.append({"label": "Biggest training week (90d)", "value": f"{row[1]:.1f}h", "sub": row[0]})

    row = conn.execute(
        "SELECT MAX(steps) FROM wellness WHERE date >= date('now','-30 days')").fetchone()
    if row and row[0]:
        out.append({"label": "Most steps (30d)", "value": f"{int(row[0]):,}", "sub": ""})
    return out


if __name__ == "__main__":
    ov = get_overview()
    print("Freshness:", ov["freshness"])
    for k, t in ov["headline"].items():
        print(f"  {t['label']}: latest={t['latest']} avg7={t['avg7']} change={t['change']}")
    print("Since:", ov["since_lines"])
    print("Highlights:", ov["highlights"])
