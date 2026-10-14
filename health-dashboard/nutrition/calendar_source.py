"""Calendar → engine events. Fetch runs in event-aggregator's venv (collectors/gcal_fetch.py);
classification here is pure and rule-driven (rules live in settings, not code)."""
from __future__ import annotations

import json
import os
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
FETCH_SCRIPT = Path(__file__).resolve().parents[1] / "collectors" / "gcal_fetch.py"
DEFAULT_EA_DIR = Path(__file__).resolve().parents[2] / "event-aggregator"


def fetch_raw(calendar_ids: list[str], start: str, end: str, timeout: int = 120) -> list[dict]:
    ea = Path(os.environ.get("EVENT_AGGREGATOR_DIR", DEFAULT_EA_DIR))
    py = ea / ".venv" / "bin" / "python"
    args = [str(py), "-I", str(FETCH_SCRIPT), "--start", start, "--end", end]
    for c in calendar_ids:
        args += ["--calendar", c]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env={**os.environ, "EVENT_AGGREGATOR_DIR": str(ea)})
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")]
    if not lines:
        raise RuntimeError(f"gcal_fetch printed no JSON (rc={proc.returncode}): {proc.stderr[-300:]}")
    out = json.loads(lines[-1])
    if not out.get("ok"):
        raise RuntimeError(out.get("error", "gcal_fetch failed"))
    return out["events"]


def _local(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt.astimezone(TZ).replace(tzinfo=None) if dt.tzinfo else dt


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _has(text: str, words: list[str]) -> bool:
    t = (text or "").lower()
    return any(w.lower() in t for w in words)


def classify(raw: list[dict], settings: dict) -> list[dict]:
    """Raw events → per-date engine events {date, start, end, title, location, busy, all_day,
    meal_out, packed_lunch, trip, workout}. Multi-day events are split per date."""
    rules = settings.get("calendar_rules", {})
    needs_action_busy = settings.get("treat_needs_action_as_busy", True)
    mo_lo, mo_hi = (int(x[:2]) * 60 + int(x[3:]) for x in rules.get("meal_out_window", ["17:00", "20:30"]))
    pl_lo, pl_hi = (int(x[:2]) * 60 + int(x[3:]) for x in rules.get("packed_lunch_span", ["11:30", "13:30"]))
    out = []
    for e in raw:
        if e.get("status") == "cancelled" or e.get("self_status") == "declined":
            continue
        if e.get("event_type") in ("workingLocation", "birthday"):
            continue
        if e.get("source_tag") == "garmin-sync":
            continue  # our own Garmin -> Calendar mirror; the planner already has these from Garmin
        title = e.get("summary") or ""
        busy = e.get("transparency", "opaque") != "transparent"
        if e.get("self_status") == "needsAction" and not needs_action_busy:
            busy = False
        trip = _has(title, rules.get("trip_keywords", []))
        workout = _has(title, rules.get("workout_keywords", []))
        if e.get("all_day"):
            d0, d1 = date.fromisoformat(e["start"][:10]), date.fromisoformat(e["end"][:10])
            d = d0
            while d < max(d1, d0 + timedelta(days=1)):
                out.append({"date": d.isoformat(), "all_day": True, "title": title, "location": e.get("location"),
                            "busy": busy and trip, "trip": trip, "workout": workout})
                d += timedelta(days=1)
            continue
        s, t = _local(e["start"]), _local(e["end"])
        d = s.date()
        while d <= t.date():
            a = s if d == s.date() else datetime.combine(d, datetime.min.time())
            b = t if d == t.date() else datetime.combine(d, datetime.max.time()).replace(microsecond=0)
            sm, em = a.hour * 60 + a.minute, min(b.hour * 60 + b.minute, 23 * 60 + 59)
            if em <= sm and d != s.date():
                d += timedelta(days=1)
                continue
            dur = em - sm
            meal_out = busy and bool(e.get("location") or _has(title, rules.get("meal_out_keywords", []))) \
                and mo_lo <= sm <= mo_hi and dur >= rules.get("meal_out_min_minutes", 90)
            packed = busy and sm <= pl_lo and em >= pl_hi and not trip
            out.append({"date": d.isoformat(), "start": _hm(sm), "end": _hm(em), "title": title,
                        "location": e.get("location"), "busy": busy, "all_day": False,
                        "meal_out": "dinner" if meal_out else None, "packed_lunch": packed,
                        "trip": trip, "workout": workout})
            d += timedelta(days=1)
    return out


def trip_sessions(events: list[dict], settings: dict) -> list[dict]:
    """Calendar trips matching settings.trip_sessions → planned sessions (one per trip date)."""
    out, seen = [], set()
    for e in events:
        if not e.get("trip") or e["date"] in seen:
            continue
        rule = next((r for r in settings.get("trip_sessions", []) if r["match"].lower() in e["title"].lower()), None)
        if not rule:
            continue
        seen.add(e["date"])
        out.append({"date": e["date"], "name": e["title"], "type": rule.get("type", "BIKEPACKING"),
                    "duration_s": int(rule.get("hours_per_day", 4) * 3600), "avg_power_w": rule.get("avg_power_w", 120),
                    "assumption": f"Assumed {rule.get('hours_per_day', 4)} h moving at ~{rule.get('avg_power_w', 120)} W "
                                  "(edit the trip rule in fuel settings). Replaced by your power data once it syncs."})
    return out
