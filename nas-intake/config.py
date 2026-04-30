"""nas-intake config — single source of truth = event-aggregator/.env on the mini.

We piggyback on event-aggregator's .env so secrets (NAS_USER, NAS_PASSWORD)
don't get duplicated. NAS_ROOT comes from there too — that file's the only
place this server stores the canonical NAS path.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
HOME_TOOLS = PROJECT_ROOT.parent  # ~/Home-Tools
EVENT_AGGREGATOR_ROOT = HOME_TOOLS / "event-aggregator"
EA_ENV_FILE = EVENT_AGGREGATOR_ROOT / ".env"
EA_VENV_PYTHON = EVENT_AGGREGATOR_ROOT / ".venv" / "bin" / "python3"

MOUNT_HELPER = HOME_TOOLS / "Mac-mini" / "scripts" / "mount-nas.sh"

STATE_PATH = PROJECT_ROOT / "state.json"
WATCHER_LOCK_PATH = PROJECT_ROOT / "watcher.lock"
LOCKS_DIR = PROJECT_ROOT / "locks"  # per-parent journal locks

# Watcher tuning
INTAKE_DEPTH_MAX = 4
DEDUP_HISTORY = 5000
SUBPROCESS_TIMEOUT_S = 600  # qwen2.5vl per-page can be 30-60s; multi-page allowance
MOUNT_HELPER_TIMEOUT_S = 30

# Large-file pipeline (escalation path — see large_file_pipeline.py)
LARGE_FILE_TRIGGER_TIMEOUTS = 3      # # of small-file timeouts before escalating
LARGE_FILE_HEARTBEAT_STALE_S = 300   # heartbeat unchanged this long → assume hung
LARGE_FILE_HEARTBEAT_POLL_S = 30     # how often the watchdog checks the heartbeat
LARGE_FILE_LOG_DIR = Path.home() / "Library" / "Logs" / "home-tools-nas-intake-large"

# File types nas-intake hands to event-aggregator
SUPPORTED_EXTS = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".gif"})
# Skip these (v1 doesn't handle HEIC; pillow-heif support lands in v2)
DEFER_EXTS = frozenset({".heic", ".heif"})


def _read_env() -> dict[str, str]:
    """Parse event-aggregator/.env into a dict. Tolerant of comments + blanks."""
    out: dict[str, str] = {}
    if not EA_ENV_FILE.exists():
        return out
    for raw in EA_ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


_env = _read_env()


def _require(key: str) -> str:
    val = _env.get(key) or os.environ.get(key, "")
    if not val:
        raise EnvironmentError(f"missing {key} in {EA_ENV_FILE}")
    return val


def _get(key: str, default: str = "") -> str:
    return _env.get(key) or os.environ.get(key, default)


# NAS access
NAS_ROOT_RAW = _get("NAS_ROOT", str(Path.home() / "Share1"))
NAS_ROOT: Path = Path(NAS_ROOT_RAW).expanduser().resolve()

NAS_USER = _get("NAS_USER", "")
NAS_IP = _get("NAS_DHCP_IPADDRESS", "")
# NAS_PASSWORD is read by mount-nas.sh, not by this code; we don't load it.
