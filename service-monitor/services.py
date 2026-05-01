"""Single source of truth for the LaunchAgents we monitor."""
import json
from dataclasses import dataclass
from paths import (
    LOG_DIR_HOME_TOOLS, LOG_DIR_HEALTH,
    LOG_PATH_DISPATCHER, LOG_PATH_FINANCE_BOT, LOG_PATH_FINANCE_WATCHER,
    LOG_PATH_NAS_INTAKE,
    PHASE6_HEARTBEAT_LOG, PHASE6_DAILY_DIGEST_LOG, PHASE6_WEEKLY_SSH_LOG,
    PHASE6_DIGEST_FAILED_FLAG,
)


@dataclass(frozen=True)
class Svc:
    id: str
    label: str
    project: str
    schedule: str
    log_path: str
    is_periodic: bool = False  # True = run-and-exit on schedule (StartInterval / StartCalendarInterval)
    plist_source_path: str | None = None  # repo-relative; preflight.py uses it to detect drift


SERVICES: list[Svc] = [
    Svc("evt_fetch",    "com.home-tools.event-aggregator.fetch",   "event-aggregator", "every 10 min",
        str(LOG_DIR_HOME_TOOLS / "event-aggregator-fetch.log"), is_periodic=True,
        plist_source_path="event-aggregator/com.home-tools.event-aggregator.fetch.plist"),
    Svc("evt_worker",   "com.home-tools.event-aggregator.worker",  "event-aggregator", "KeepAlive",
        str(LOG_DIR_HOME_TOOLS / "event-aggregator-worker.log"),
        plist_source_path="event-aggregator/com.home-tools.event-aggregator.worker.plist"),
    Svc("disp",         "com.home-tools.dispatcher",                "dispatcher",       "KeepAlive",
        str(LOG_PATH_DISPATCHER),
        plist_source_path="dispatcher/com.home-tools.dispatcher.plist"),
    Svc("fin_bot",      "com.home-tools.finance-monitor",           "finance-monitor",  "KeepAlive",
        str(LOG_PATH_FINANCE_BOT),
        plist_source_path="finance-monitor/com.home-tools.finance-monitor.plist"),
    Svc("fin_watcher",  "com.home-tools.finance-monitor-watcher",   "finance-monitor",  "every 5 min",
        str(LOG_PATH_FINANCE_WATCHER), is_periodic=True,
        plist_source_path="finance-monitor/com.home-tools.finance-monitor-watcher.plist"),
    Svc("hd_receiver",  "com.health-dashboard.receiver",            "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "receiver.log"),
        plist_source_path="health-dashboard/config/com.health-dashboard.receiver.plist"),
    Svc("hd_collect",   "com.health-dashboard.collect",             "health-dashboard", "7:00 + 7:20 daily",
        str(LOG_DIR_HEALTH / "collect.log"), is_periodic=True,
        plist_source_path="health-dashboard/config/com.health-dashboard.collect.plist"),
    Svc("hd_intervals", "com.health-dashboard.intervals-poll",      "health-dashboard", "every 5 min",
        str(LOG_DIR_HEALTH / "intervals-poll.log"), is_periodic=True,
        plist_source_path="health-dashboard/config/com.health-dashboard.intervals-poll.plist"),
    Svc("hd_staleness", "com.health-dashboard.staleness",           "health-dashboard", "7:00 + 21:00",
        str(LOG_DIR_HEALTH / "staleness.log"), is_periodic=True,
        plist_source_path="health-dashboard/config/com.health-dashboard.staleness.plist"),
    Svc("hd_streamlit", "com.health-dashboard.streamlit",           "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "streamlit.log"),
        plist_source_path="health-dashboard/config/com.health-dashboard.streamlit.plist"),
    Svc("svc_monitor",  "com.home-tools.service-monitor",           "service-monitor",  "KeepAlive (self)",
        str(LOG_DIR_HOME_TOOLS / "service-monitor.log"),
        plist_source_path="service-monitor/config/com.home-tools.service-monitor.plist"),
    Svc("nas_intake",   "com.home-tools.nas-intake",                "nas-intake",       "every 5 min",
        str(LOG_PATH_NAS_INTAKE), is_periodic=True,
        plist_source_path="nas-intake/com.home-tools.nas-intake.plist"),
    # Phase 6 (Mac mini monitoring layer) — see Mac-mini/PHASE6.md
    Svc("p6_heartbeat", "com.home-tools.heartbeat",                 "phase6",           "every 30 min",
        str(PHASE6_HEARTBEAT_LOG), is_periodic=True,
        plist_source_path="Mac-mini/LaunchAgents/com.home-tools.heartbeat.plist"),
    Svc("p6_daily",     "com.home-tools.daily-digest",              "phase6",           "07:00 daily",
        str(PHASE6_DAILY_DIGEST_LOG), is_periodic=True,
        plist_source_path="Mac-mini/LaunchAgents/com.home-tools.daily-digest.plist"),
    Svc("p6_weekly_ssh","com.home-tools.weekly-ssh-digest",         "phase6",           "Mon 09:00",
        str(PHASE6_WEEKLY_SSH_LOG), is_periodic=True,
        plist_source_path="Mac-mini/LaunchAgents/com.home-tools.weekly-ssh-digest.plist"),
]


# Agents that intentionally exist on the mini but are NOT in SERVICES.
# preflight.py uses this to silence false-positive "loaded but not in SERVICES"
# warnings. Add a one-line reason — if you can't articulate the reason, it
# probably belongs in SERVICES.
KNOWN_UNMONITORED_LABELS: dict[str, str] = {
    "com.home-tools.dispatcher-3day-check":
        "Probe agent — runs the 3-day dispatcher health check on a schedule, output goes to Slack, not monitored as a service",
    "com.home-tools.memory-tracker":
        "Tier-2 memory observer — writes JSON to ~/Library/Application Support/, surfaced in service-monitor app, not a Svc itself",
    "com.home-tools.ollama-tracker":
        "Tier-2 Ollama observer — writes JSON to ~/Library/Application Support/, surfaced in service-monitor app, not a Svc itself",
    "com.home-tools.imessage-export":
        "Laptop-only — exports iMessage SQLite to JSONL for the mini event-aggregator to ingest; never runs on the mini",
}

SERVICES_BY_ID = {s.id: s for s in SERVICES}
SERVICES_BY_LABEL = {s.label: s for s in SERVICES}


def digest_failed_flag() -> dict | None:
    """Return parsed flag if Phase 6's daily-digest failed to deliver to Slack.

    The flag is written by Mac-mini/scripts/slack-post.sh on any non-200 from
    chat.postMessage and cleared on the next successful post. Surfaces silent
    Slack-side failures on the service-monitor dashboard so the user notices
    even when Slack itself isn't delivering messages.

    Returns the parsed flag dict (with ts/channel/rc/err_raw keys), or None
    if the flag file doesn't exist or can't be parsed.
    """
    if not PHASE6_DIGEST_FAILED_FLAG.exists():
        return None
    try:
        return json.loads(PHASE6_DIGEST_FAILED_FLAG.read_text())
    except (OSError, json.JSONDecodeError):
        return {"err_raw": "flag exists but unparseable"}
