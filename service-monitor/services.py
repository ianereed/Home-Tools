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

# Phase 7 log paths — derived from LOG_DIR_HOME_TOOLS to match Phase 6 convention.
PHASE7_RESTIC_HOURLY_LOG = LOG_DIR_HOME_TOOLS / "restic-hourly.log"
PHASE7_RESTIC_DAILY_LOG = LOG_DIR_HOME_TOOLS / "restic-daily.log"
PHASE7_RESTIC_PRUNE_LOG = LOG_DIR_HOME_TOOLS / "restic-prune.log"


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
    # KeepAlive listeners that didn't migrate (no jobs-style cadence).
    # evt_fetch migrated to jobs framework in Phase 12.5 (kind: event_aggregator_fetch).
    # evt_worker migrating to jobs framework in Phase 12.7 (kinds: event_aggregator_text,
    # event_aggregator_vision, event_aggregator_decision_poller). Plist disabled at cutover.
    Svc("evt_worker",   "com.home-tools.event-aggregator.worker",  "event-aggregator", "KeepAlive (→ jobs 12.7)",
        str(LOG_DIR_HOME_TOOLS / "event-aggregator-worker.log"),
        plist_source_path="event-aggregator/com.home-tools.event-aggregator.worker.plist"),
    Svc("disp",         "com.home-tools.dispatcher",                "dispatcher",       "KeepAlive",
        str(LOG_PATH_DISPATCHER),
        plist_source_path="dispatcher/com.home-tools.dispatcher.plist"),
    Svc("fin_bot",      "com.home-tools.finance-monitor",           "finance-monitor",  "KeepAlive",
        str(LOG_PATH_FINANCE_BOT),
        plist_source_path="finance-monitor/com.home-tools.finance-monitor.plist"),
    Svc("hd_receiver",  "com.health-dashboard.receiver",            "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "receiver.log"),
        plist_source_path="health-dashboard/config/com.health-dashboard.receiver.plist"),
    Svc("hd_streamlit", "com.health-dashboard.streamlit",           "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "streamlit.log"),
        plist_source_path="health-dashboard/config/com.health-dashboard.streamlit.plist"),
    Svc("svc_monitor",  "com.home-tools.service-monitor",           "service-monitor",  "KeepAlive (self)",
        str(LOG_DIR_HOME_TOOLS / "service-monitor.log"),
        plist_source_path="service-monitor/config/com.home-tools.service-monitor.plist"),
    # Phase 12 — Mini Jobs framework (replaces 12 cron-style LaunchAgents).
    Svc("jobs_consumer", "com.home-tools.jobs-consumer",            "jobs",             "KeepAlive (huey)",
        str(LOG_DIR_HOME_TOOLS / "jobs-consumer.log"),
        plist_source_path="jobs/config/com.home-tools.jobs-consumer.plist"),
    Svc("jobs_http",     "com.home-tools.jobs-http",                "jobs",             "KeepAlive (:8504)",
        str(LOG_DIR_HOME_TOOLS / "jobs-http.log"),
        plist_source_path="jobs/config/com.home-tools.jobs-http.plist"),
    Svc("console",       "com.home-tools.console",                  "console",          "KeepAlive (:8503)",
        str(LOG_DIR_HOME_TOOLS / "console.log"),
        plist_source_path="console/config/com.home-tools.console.plist"),
]


# Agents that intentionally exist on the mini but are NOT in SERVICES.
# preflight.py uses this to silence false-positive "loaded but not in SERVICES"
# warnings. Add a one-line reason — if you can't articulate the reason, it
# probably belongs in SERVICES.
KNOWN_UNMONITORED_LABELS: dict[str, str] = {
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
