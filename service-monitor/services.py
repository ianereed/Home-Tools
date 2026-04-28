"""Single source of truth for the LaunchAgents we monitor."""
from dataclasses import dataclass
from paths import (
    LOG_DIR_HOME_TOOLS, LOG_DIR_HEALTH,
    LOG_PATH_DISPATCHER, LOG_PATH_FINANCE_BOT, LOG_PATH_FINANCE_WATCHER,
)


@dataclass(frozen=True)
class Svc:
    id: str
    label: str
    project: str
    schedule: str
    log_path: str
    is_periodic: bool = False  # True = run-and-exit on schedule (StartInterval / StartCalendarInterval)


SERVICES: list[Svc] = [
    Svc("evt_fetch",    "com.home-tools.event-aggregator.fetch",   "event-aggregator", "every 10 min",
        str(LOG_DIR_HOME_TOOLS / "event-aggregator-fetch.log"), is_periodic=True),
    Svc("evt_worker",   "com.home-tools.event-aggregator.worker",  "event-aggregator", "KeepAlive",
        str(LOG_DIR_HOME_TOOLS / "event-aggregator-worker.log")),
    Svc("disp",         "com.home-tools.dispatcher",                "dispatcher",       "KeepAlive",
        str(LOG_PATH_DISPATCHER)),
    Svc("fin_bot",      "com.home-tools.finance-monitor",           "finance-monitor",  "KeepAlive",
        str(LOG_PATH_FINANCE_BOT)),
    Svc("fin_watcher",  "com.home-tools.finance-monitor-watcher",   "finance-monitor",  "every 5 min",
        str(LOG_PATH_FINANCE_WATCHER), is_periodic=True),
    Svc("hd_receiver",  "com.health-dashboard.receiver",            "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "receiver.log")),
    Svc("hd_collect",   "com.health-dashboard.collect",             "health-dashboard", "7:00 + 7:20 daily",
        str(LOG_DIR_HEALTH / "collect.log"), is_periodic=True),
    Svc("hd_intervals", "com.health-dashboard.intervals-poll",      "health-dashboard", "every 5 min",
        str(LOG_DIR_HEALTH / "intervals-poll.log"), is_periodic=True),
    Svc("hd_staleness", "com.health-dashboard.staleness",           "health-dashboard", "7:00 + 21:00",
        str(LOG_DIR_HEALTH / "staleness.log"), is_periodic=True),
    Svc("hd_streamlit", "com.health-dashboard.streamlit",           "health-dashboard", "KeepAlive",
        str(LOG_DIR_HEALTH / "streamlit.log")),
    Svc("svc_monitor",  "com.home-tools.service-monitor",           "service-monitor",  "KeepAlive (self)",
        str(LOG_DIR_HOME_TOOLS / "service-monitor.log")),
]

SERVICES_BY_ID = {s.id: s for s in SERVICES}
SERVICES_BY_LABEL = {s.label: s for s in SERVICES}
