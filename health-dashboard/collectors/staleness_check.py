"""Check for stale health data, diagnose the cause, and send a push notification via ntfy.sh."""

import sqlite3
import subprocess
from datetime import datetime, timedelta

from .db import DB_PATH

# ntfy.sh topic — subscribe to this in the ntfy app on your phone
NTFY_TOPIC = "ian-health-dashboard"
STALE_THRESHOLD_HOURS = 24


def check_staleness() -> list[str]:
    """Return list of stale data source descriptions."""
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now()
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

    conn.close()
    return stale


def diagnose() -> str:
    """Check each link in the data chain and return troubleshooting steps."""
    problems = []
    steps = []

    # 1. Is the receiver process running?
    result = subprocess.run(
        ["lsof", "-i", ":8095", "-sTCP:LISTEN"],
        capture_output=True, text=True,
    )
    if not result.stdout.strip():
        problems.append("Receiver is DOWN")
        steps.append("On your Mac, run: launchctl kickstart -k gui/$(id -u)/com.health-dashboard.receiver")
    else:
        # 2. Is Tailscale running on the Mac?
        result = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            problems.append("Tailscale is not running on Mac")
            steps.append("Open Tailscale on your Mac and connect")
        else:
            import json
            try:
                ts = json.loads(result.stdout)
                # Check if any peer is the iPhone
                peers = ts.get("Peer", {})
                iphone_online = False
                for peer_id, peer in peers.items():
                    if "iphone" in (peer.get("HostName", "") or "").lower():
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
    if stale:
        print(f"Stale sources: {', '.join(stale)}")
        send_notification(stale)
        print("Notification sent.")
    else:
        print("All data is fresh.")


if __name__ == "__main__":
    main()
