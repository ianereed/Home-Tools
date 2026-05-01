"""Host-aware paths. Dashboard runs on the laptop during dev and on the mini in prod."""
import socket
from pathlib import Path

_HOST = socket.gethostname()
ON_MINI = _HOST.startswith("homeserver")

if ON_MINI:
    HOME = Path("/Users/homeserver")
    REPO = HOME / "Home-Tools"
    LOG_DIR_HOME_TOOLS = HOME / "Library/Logs/home-tools"
    LOG_DIR_HEALTH = HOME / "Library/Logs/health-dashboard"
    LOG_PATH_DISPATCHER = HOME / "Library/Logs/home-tools-dispatcher.log"
    LOG_PATH_FINANCE_BOT = HOME / "Library/Logs/home-tools-finance-monitor.log"
    LOG_PATH_FINANCE_WATCHER = HOME / "Library/Logs/home-tools-finance-monitor-watcher.log"
else:
    HOME = Path.home()
    REPO = HOME / "Documents/GitHub/Home-Tools"
    LOG_DIR_HOME_TOOLS = HOME / "Library/Logs/home-tools"
    LOG_DIR_HEALTH = HOME / "Library/Logs/health-dashboard"
    LOG_PATH_DISPATCHER = HOME / "Library/Logs/home-tools-dispatcher.log"
    LOG_PATH_FINANCE_BOT = HOME / "Library/Logs/home-tools-finance-monitor.log"
    LOG_PATH_FINANCE_WATCHER = HOME / "Library/Logs/home-tools-finance-monitor-watcher.log"

EVT_STATE_PATH = REPO / "event-aggregator/state.json"
NAS_INTAKE_STATE_PATH = REPO / "nas-intake/state.json"
HEALTH_DB_PATH = REPO / "health-dashboard/data/health.db"
FINANCE_DB_PATH = REPO / "finance-monitor/data/finance.db"

if ON_MINI:
    LOG_PATH_NAS_INTAKE = HOME / "Library/Logs/home-tools-nas-intake.log"
else:
    LOG_PATH_NAS_INTAKE = HOME / "Library/Logs/home-tools-nas-intake.log"

# Phase 6 (Mac mini monitoring) — see Mac-mini/PHASE6.md
PHASE6_HEARTBEAT_LOG = LOG_DIR_HOME_TOOLS / "heartbeat.log"
PHASE6_DAILY_DIGEST_LOG = LOG_DIR_HOME_TOOLS / "daily-digest.log"
PHASE6_WEEKLY_SSH_LOG = LOG_DIR_HOME_TOOLS / "weekly-ssh-digest.log"
PHASE6_INCIDENTS_PATH = REPO / "logs/incidents.jsonl"
PHASE6_DIGEST_FAILED_FLAG = REPO / "run/digest-failed.flag"
