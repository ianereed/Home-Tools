"""Check for stale health data, diagnose the cause, and send a push notification via ntfy.sh."""

import os
import sqlite3
import subprocess
from datetime import datetime, timedelta

from .db import DB_PATH

# ntfy.sh topic — subscribe to this in the ntfy app on your phone
NTFY_TOPIC = "ian-health-dashboard"
STALE_THRESHOLD_HOURS = 24

# Sparse-metric thresholds (days). BP/weight aren't daily-cadence like sleep/HR,
# so a fixed 24h window would page Ian every morning before he even owns a scale.
BP_STALE_DAYS = 14
BP_DORMANT_DAYS = 60
WEIGHT_STALE_DAYS = 10
WEIGHT_DORMANT_DAYS = 45
# Nutrition: meal logging is daily-cadence once the habit exists, but a missed
# weekend shouldn't page — stale at 4 days. If logging is abandoned (the known
# risk with any food tracker), go dormant after 30 days and stop nagging.
NUTRITION_STALE_DAYS = 4
NUTRITION_DORMANT_DAYS = 30

# Heartbeat log. The health_staleness huey kind uses this file's mtime as its
# migration baseline metric (file-mtime:logs/health-staleness.log), so every run
# must touch it — otherwise the verifier thinks the job stopped firing.
LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "health-staleness.log")


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    """Run a subprocess, tolerating a missing binary.

    Under launchd the PATH may lack tools like `tailscale` (/opt/homebrew/bin);
    a missing binary should degrade the diagnosis, not crash the whole check
    with a FileNotFoundError (which previously surfaced as rc=1 under the
    consumer only when data was actually stale).
    """
    try:
        return subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return None


def _write_heartbeat(stale_sources: list[str]) -> None:
    """Append a one-line result to the heartbeat log (creates logs/ if needed)."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    status = "STALE: " + ", ".join(stale_sources) if stale_sources else "OK"
    with open(LOG_FILE, "a") as fh:
        fh.write(f"{datetime.now().isoformat(timespec='seconds')} {status}\n")


def _sparse_metric_alert(
    conn: sqlite3.Connection,
    table: str,
    source_filter: str | None,
    stale_days: int,
    dormant_days: int,
    label: str,
    now: datetime,
    ts_column: str = "timestamp",
) -> str | None:
    """Armed / stale / dormant staleness for a sparse metric (BP, weight, nutrition).

    A metric that has never produced a (filtered) row is *unarmed* — no device
    exists yet, so there's nothing to nag about (e.g. weight arms only on
    source='garmin' rows, so pre-scale Apple/DEXA anchors can never trigger a
    "no weigh-in" alarm). Once armed, a row older than `dormant_days` silences
    it again (habit abandoned — stop nagging); an alert fires only in the
    stale_days..dormant_days window. Queries are wrapped so a pre-migration DB
    (missing the cardio tables) degrades to "not armed" instead of crashing the
    whole staleness check. `ts_column` covers date-keyed tables like
    nutrition_daily (a bare YYYY-MM-DD parses as midnight, which is fine at
    multi-day thresholds).
    """
    try:
        if source_filter is not None:
            row = conn.execute(
                f"SELECT MAX({ts_column}) FROM {table} WHERE source = ?", (source_filter,)
            ).fetchone()
        else:
            row = conn.execute(f"SELECT MAX({ts_column}) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        return None

    last = row[0] if row else None
    if not last:
        return None  # unarmed: no data ever recorded

    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return None

    age_days = (now - last_dt).total_seconds() / 86400
    if age_days > dormant_days:
        return None  # dormant: habit abandoned, stop nagging
    if age_days > stale_days:
        return f"{label} (last: {last[:10]})"
    return None


def check_staleness(now: datetime | None = None) -> list[str]:
    """Return list of stale data source descriptions."""
    now = now or datetime.now()
    conn = sqlite3.connect(DB_PATH)
    cutoff = now - timedelta(hours=STALE_THRESHOLD_HOURS)
    cutoff_date = cutoff.strftime("%Y-%m-%d")
    cutoff_ts = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    stale = []

    # Sleep
    row = conn.execute("SELECT date FROM sleep ORDER BY date DESC LIMIT 1").fetchone()
    if not row or row[0] < cutoff_date:
        last = row[0] if row else "never"
        stale.append(f"Sleep (last: {last})")

    # Heart rate
    row = conn.execute(
        "SELECT timestamp FROM heart_rate WHERE context = 'resting' ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if not row or row[0] < cutoff_ts:
        last = row[0][:10] if row else "never"
        stale.append(f"Resting HR (last: {last})")

    # HRV
    row = conn.execute(
        "SELECT date FROM wellness WHERE hrv IS NOT NULL ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row or row[0] < cutoff_date:
        last = row[0] if row else "never"
        stale.append(f"HRV (last: {last})")

    # Blood pressure (armed on any source; sparse — cuff or manual entries).
    bp_alert = _sparse_metric_alert(
        conn, "blood_pressure", None, BP_STALE_DAYS, BP_DORMANT_DAYS, "Blood pressure", now
    )
    if bp_alert:
        stale.append(bp_alert)

    # Weight — arms only on source='garmin' rows, so Apple historical anchors
    # and quarterly DEXA scans can never trigger "no weigh-in" before the
    # Garmin scale exists (or on the weeks between DEXA scans afterward).
    weight_alert = _sparse_metric_alert(
        conn, "body_weight", "garmin", WEIGHT_STALE_DAYS, WEIGHT_DORMANT_DAYS, "Weight", now
    )
    if weight_alert:
        stale.append(weight_alert)

    # Nutrition — arms only on source='garmin' rows (the Connect+ food log),
    # so a hypothetical future Apple-path backfill can't arm it prematurely.
    nutrition_alert = _sparse_metric_alert(
        conn, "nutrition_daily", "garmin", NUTRITION_STALE_DAYS,
        NUTRITION_DORMANT_DAYS, "Nutrition log", now, ts_column="date",
    )
    if nutrition_alert:
        stale.append(nutrition_alert)

    conn.close()
    return stale


def diagnose() -> str:
    """Check each link in the data chain and return troubleshooting steps."""
    problems = []
    steps = []

    # 1. Is the receiver process running?
    result = _run(["lsof", "-i", ":8095", "-sTCP:LISTEN"])
    if result is None:
        # lsof unavailable — can't probe the port; skip to app-side guidance.
        problems.append("Could not probe receiver (lsof unavailable)")
        steps.append("On your iPhone: open Health Auto Export and tap Export Now")
        return problems, steps
    if not result.stdout.strip():
        problems.append("Receiver is DOWN")
        steps.append("On your Mac, run: launchctl kickstart -k gui/$(id -u)/com.health-dashboard.receiver")
    else:
        # 2. Is Tailscale running on the Mac?
        result = _run(["tailscale", "status", "--json"])
        if result is None:
            problems.append("Could not check Tailscale (tailscale CLI unavailable)")
            steps.append("Verify Tailscale is connected on both Mac and iPhone")
        elif result.returncode != 0:
            problems.append("Tailscale is not running on Mac")
            steps.append("Open Tailscale on your Mac and connect")
        else:
            import json
            try:
                ts = json.loads(result.stdout)
                # Check if any peer is the iPhone. NOTE: iOS reports HostName as
                # "localhost", so we match on DNSName / OS instead — matching on
                # HostName="iphone" silently never fires and mis-reports offline.
                peers = ts.get("Peer", {})
                iphone_online = False
                for peer_id, peer in peers.items():
                    dns = (peer.get("DNSName", "") or "").lower()
                    os_name = (peer.get("OS", "") or "").lower()
                    if os_name == "ios" or "iphone" in dns:
                        iphone_online = peer.get("Online", False)
                        break

                if not iphone_online:
                    problems.append("iPhone is offline on Tailscale")
                    steps.append("On your iPhone: open Tailscale and make sure it's connected")
                    steps.append("If you see 'DNS unavailable': disconnect and reconnect Tailscale on iPhone")
                else:
                    # Tailscale is fine, receiver is fine — problem is on the app side
                    problems.append("Receiver + Tailscale look fine")
                    steps.append("On your iPhone: open Health Auto Export and tap Export Now")
                    steps.append("If stuck at 0%: force-close Health Auto Export and reopen, then export again")
                    steps.append("If still stuck: check Tailscale app on iPhone for DNS errors, toggle off/on")
            except (json.JSONDecodeError, KeyError):
                problems.append("Couldn't read Tailscale status")
                steps.append("Check Tailscale on both Mac and iPhone")

    return problems, steps


def send_notification(stale_sources: list[str]):
    """Send push notification via ntfy.sh with diagnosis."""
    problems, steps = diagnose()

    title = "Health Dashboard: Stale Data"
    body = "Stale sources (24h+):\n" + "\n".join(f"  - {s}" for s in stale_sources)
    body += "\n\nDiagnosis:\n" + "\n".join(f"  - {p}" for p in problems)
    body += "\n\nTroubleshoot:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps))

    subprocess.run(
        [
            "curl", "-s",
            "-H", f"Title: {title}",
            "-H", "Priority: default",
            "-H", "Tags: warning",
            "-d", body,
            f"https://ntfy.sh/{NTFY_TOPIC}",
        ],
        capture_output=True,
    )


def main():
    stale = check_staleness()
    _write_heartbeat(stale)
    if stale:
        print(f"Stale sources: {', '.join(stale)}")
        send_notification(stale)
        print("Notification sent.")
    else:
        print("All data is fresh.")


if __name__ == "__main__":
    main()
