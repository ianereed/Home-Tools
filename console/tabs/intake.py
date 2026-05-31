"""Intake tab — upload files into the NAS intake drop zones.

Two kinds of destination show up here:

1. Every directory named `intake` under the NAS root (depth <= 4). `nas-intake`
   watches these; each one's parent is the *filing scope*. Files dropped here
   run through nas-intake's OCR/file/journal pipeline.

2. The recipe **photo-intake** drop zone
   (`<NAS>/Documents/Recipes/photo-intake`). It isn't named `intake`, so it's
   added as a special case. The `meal_planner_photo_intake_scan` huey task
   watches it and routes files through the meal-planner local **llama3.2-vision**
   (Ollama) pipeline — a different LLM workflow from the rest. Accepts photos,
   HEIC, and PDF (HEIC/PDF are converted to an image before extraction).

Either way the user just drops a file and the relevant watcher picks it up
within ~5 min.

Discovery is reimplemented here (not imported from `nas-intake/`): that dir is
hyphenated — not importable as a module — and its `config.py` does a bare
`import config`. The walk is short stdlib; see `nas-intake/discovery.py` for the
canonical copy the watcher uses.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import streamlit as st


# Canonical NAS path lives in event-aggregator/.env (see nas-intake/config.py).
_EA_ENV_FILE = Path(__file__).resolve().parents[2] / "event-aggregator" / ".env"
_DEFAULT_NAS_ROOT = Path.home() / "Share1"

# Mirror nas-intake/discovery.py + config.py.
INTAKE_DEPTH_MAX = 4
_SKIP_DIRS = frozenset({
    "@eaDir",            # Synology indexing
    "#recycle",          # Synology recycle bin
    ".Trashes",          # macOS
    ".Spotlight-V100",
    ".fseventsd",
    "_processed", "_quarantine", "_review",  # nas-intake's own subfolders
})

_ACCEPT_EXTS = ["png", "jpg", "jpeg", "heic", "pdf"]

# Recipe photo-intake — watched by jobs/kinds/meal_planner_photo_intake_scan.py,
# which feeds the meal-planner local llama3.2-vision (Ollama) pipeline.
_RECIPE_PHOTO_SUBPATH = ("Documents", "Recipes", "photo-intake")
_PHOTO_EXTS = ["jpg", "jpeg", "png", "heic", "heif", "pdf"]
_RECIPE_NOTE = (
    "📸 Recipe files → meal-planner local **llama3.2-vision** (Ollama) pipeline "
    "(a different workflow from the standard OCR intake). "
    "Photos, HEIC, and PDF — HEIC/PDF are converted to an image first."
)
_NAS_PICKUP = "The nas-intake watcher will pick them up within ~5 min."
_RECIPE_PICKUP = "The recipe photo scanner will pick them up within ~5 min (llama3.2-vision)."


class _Dest(NamedTuple):
    """An upload destination rendered as one section on the tab."""
    path: Path
    accept: list[str]
    note: str | None
    pickup: str  # tail of the success message


def _resolve_nas_root() -> Path:
    """NAS_ROOT from event-aggregator/.env, defaulting to ~/Share1."""
    try:
        for raw in _EA_ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == "NAS_ROOT":
                val = v.strip().strip('"').strip("'")
                if val:
                    return Path(val).expanduser()
    except OSError:
        pass
    return _DEFAULT_NAS_ROOT


def _recipe_photo_dir(root: Path) -> Path:
    """Recipe photo-intake drop zone. Honors MEAL_PLANNER_NAS_INTAKE_DIR (the
    same env the scan task reads), else `<NAS>/Documents/Recipes/photo-intake`.
    """
    env = os.environ.get("MEAL_PLANNER_NAS_INTAKE_DIR")
    if env:
        return Path(env).expanduser()
    return root.joinpath(*_RECIPE_PHOTO_SUBPATH)


def _skip(name: str) -> bool:
    return name.startswith(".") or name.startswith("._") or name in _SKIP_DIRS


def find_intakes(root: Path, max_depth: int = INTAKE_DEPTH_MAX) -> list[Path]:
    """Every dir named `intake` (case-insensitive) under `root`, depth-capped.

    Does not descend into a matched intake dir (its subdirs are processing
    buckets). Port of nas-intake/discovery.py:find_intakes.
    """
    if not root.exists() or not root.is_dir():
        return []
    found: list[Path] = []

    def _walk(d: Path, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or _skip(entry.name):
                continue
            if entry.name.lower() == "intake":
                found.append(entry)
                continue  # don't recurse into an intake/
            _walk(entry, depth + 1)

    _walk(root, depth=1)
    return found


def breadcrumb(intake: Path, root: Path) -> str:
    """`Share1 > Healthcare > 0-Ian Healthcare > Intake` for an intake path."""
    rel = intake.relative_to(root)
    return " > ".join([root.name, *rel.parts])


def _slug(intake: Path, root: Path) -> str:
    """Stable widget-key suffix from the path relative to root."""
    rel = "_".join(intake.relative_to(root).parts)
    return re.sub(r"[^A-Za-z0-9]+", "_", rel).strip("_").lower()


# ── Persistent folder cache + background discovery ───────────────────────────
#
# The SMB walk is slow (~25s, ~600 dir stats, for ~3 folders) and the folder set
# is near-static. So we never walk on the render path. Instead:
#
#   • `intake_cache.json` (local disk, gitignored) holds the last-known folder
#     list. render() paints from it instantly.
#   • A daemon thread re-walks in the background when the cache is stale, writes
#     the JSON, and flips a process-level flag. New folders appear on the next
#     rerun; vanished folders are pruned (verified by a direct per-folder stat,
#     so a transient/partial walk never blanks the known list).
#   • A `st.fragment` polls that flag and shows a prominent "scanning" bar.
_CACHE_FILE = Path(__file__).resolve().parents[1] / "intake_cache.json"
_REFRESH_AFTER_S = 600  # re-scan in the background if the cache is older than this

# Process-global scan state (single-user dashboard; survives reruns, not restart).
_SCAN_LOCK = threading.Lock()
_SCAN_STATE: dict = {"running": False, "finished_at": None, "count": None, "error": None}


def _load_cache() -> dict | None:
    """Last-known folder list, or None if missing/corrupt."""
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_cache(root_str: str, folders: list[str], recipe_dir: str | None) -> None:
    """Atomically persist the discovered destinations."""
    payload = {
        "root": root_str,
        "folders": folders,
        "recipe_dir": recipe_dir,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = _CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(_CACHE_FILE)


def _cache_age_seconds(cache: dict) -> float:
    """Seconds since the cache was written; +inf if unparseable."""
    try:
        scanned = datetime.fromisoformat(cache["scanned_at"])
        return (datetime.now(timezone.utc) - scanned).total_seconds()
    except (KeyError, ValueError):
        return float("inf")


def _scan(root_str: str) -> None:
    """Background thread: walk the NAS, merge+prune, persist, flag completion.

    Does NOT call any `st.*` (runs without a ScriptRunContext). Merge rule: the
    new walk's results are unioned with the previously-known list, then every
    entry is verified with a direct `.is_dir()` stat. So a fresh folder is added,
    a genuinely-removed folder is pruned, but a partial walk that misses an
    existing folder can't delete it.
    """
    try:
        root = Path(root_str)
        if not root.exists() or not root.is_dir():
            raise OSError(f"NAS root not accessible: {root}")
        walked = {str(p) for p in find_intakes(root)}
        cache = _load_cache() or {}
        known = set(cache.get("folders", [])) if cache.get("root") == root_str else set()
        alive = sorted(p for p in (walked | known) if Path(p).is_dir())
        recipe = _recipe_photo_dir(root)
        recipe_dir = str(recipe) if recipe.exists() and recipe.is_dir() else None
        _write_cache(root_str, alive, recipe_dir)
        with _SCAN_LOCK:
            _SCAN_STATE.update(running=False, finished_at=time.time(), count=len(alive), error=None)
    except Exception as exc:  # noqa: BLE001 — surface any failure on the bar
        with _SCAN_LOCK:
            _SCAN_STATE.update(running=False, finished_at=time.time(), error=str(exc))


def _start_scan(root_str: str) -> None:
    """Kick a background walk unless one is already in flight."""
    with _SCAN_LOCK:
        if _SCAN_STATE["running"]:
            return
        _SCAN_STATE.update(running=True, finished_at=None, count=None, error=None)
    threading.Thread(target=_scan, args=(root_str,), daemon=True).start()


def _render_recent(intake: Path) -> None:
    try:
        entries = sorted(intake.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError as exc:
        st.caption(f"could not list folder: {exc}")
        return
    entries = [p for p in entries if p.is_file() and not p.name.startswith(".")][:10]
    if not entries:
        st.caption("(no files queued here)")
        return
    rows = [{
        "name": p.name,
        "size (bytes)": p.stat().st_size,
        "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
    } for p in entries]
    st.dataframe(rows, hide_index=True, use_container_width=True)


def _render_destination(dest: _Dest, root: Path) -> None:
    crumb = breadcrumb(dest.path, root)
    kinds = "image / PDF" if "pdf" in dest.accept else "image"
    with st.container(border=True):
        st.markdown(f"**{crumb}**")
        if dest.note:
            st.caption(dest.note)
        slug = _slug(dest.path, root)
        files = st.file_uploader(
            f"Select one or more files ({kinds})",
            type=dest.accept,
            accept_multiple_files=True,
            key=f"uploader_{slug}",
            label_visibility="collapsed",
        )
        if st.button("Upload", key=f"upload_btn_{slug}"):
            if not files:
                st.warning("Select a file first.")
            else:
                written = []
                for f in files:
                    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                    target = dest.path / f"{ts}_{f.name}"
                    target.write_bytes(f.getbuffer())
                    written.append((target.name, target.stat().st_size))
                total = sum(sz for _, sz in written)
                st.success(
                    f"Uploaded {len(written)} file(s) ({total:,} bytes) to {crumb}. "
                    f"{dest.pickup}"
                )
        with st.expander("Recent files in this folder"):
            _render_recent(dest.path)


def _fmt_age(scanned_at: str) -> str:
    """Human age for a cache `scanned_at` ISO timestamp."""
    try:
        secs = (datetime.now(timezone.utc) - datetime.fromisoformat(scanned_at)).total_seconds()
    except ValueError:
        return "unknown"
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    return f"{int(secs // 3600)} h ago"


@st.fragment(run_every="2s")
def _scan_status() -> None:
    """Prominent scanning bar. Polls the background-scan flag every 2s; shows a
    running spinner while a walk is in flight, then a brief result banner. On a
    successful completion it reruns the whole tab once so new folders render."""
    with _SCAN_LOCK:
        running = _SCAN_STATE["running"]
        finished_at = _SCAN_STATE["finished_at"]
        count = _SCAN_STATE["count"]
        error = _SCAN_STATE["error"]

    if running:
        st.status("Scanning the NAS for intake folders…", state="running")
        return
    if finished_at is None:
        return  # no scan has run in this process yet

    # Surface a brand-new completion to the whole app exactly once, so the
    # main body re-renders against the freshly-written cache.
    if st.session_state.get("_intake_scan_seen") != finished_at:
        st.session_state["_intake_scan_seen"] = finished_at
        if not error:
            st.rerun(scope="app")

    if time.time() - finished_at <= 8:  # let the banner linger briefly, then idle
        if error:
            st.warning(f"NAS scan failed: {error}", icon="⚠️")
        else:
            st.success(f"Found {count} intake folder(s).", icon="✅")


def render() -> None:
    nas_root = _resolve_nas_root()
    if not nas_root.exists() or not nas_root.is_dir():
        st.warning(
            f"NAS not present at `{nas_root}`. "
            "(Either the SMB mount isn't up, or you're running this off the mini.)"
        )
        return

    cache = _load_cache()
    fresh = cache is not None and cache.get("root") == str(nas_root)

    # Kick a background re-walk when we have nothing cached or it's gone stale.
    if not fresh or _cache_age_seconds(cache) > _REFRESH_AFTER_S:
        _start_scan(str(nas_root))

    _scan_status()

    top = st.columns([1, 4])
    with top[0]:
        if st.button("↻ Refresh folders", use_container_width=True):
            _start_scan(str(nas_root))
            st.rerun()
    with top[1]:
        cap = (
            f"`intake/` folders under `{nas_root}` (depth ≤ {INTAKE_DEPTH_MAX}) "
            "plus the recipe photo-intake drop zone."
        )
        if fresh and cache.get("scanned_at"):
            cap += f"  ·  last scan {_fmt_age(cache['scanned_at'])}"
        st.caption(cap)

    if not fresh:
        st.info("First scan of the NAS in progress — folders will appear in a moment…")
        return

    dests = [_Dest(Path(s), _ACCEPT_EXTS, None, _NAS_PICKUP) for s in cache.get("folders", [])]
    recipe_dir = cache.get("recipe_dir")
    if recipe_dir:
        dests.append(_Dest(Path(recipe_dir), _PHOTO_EXTS, _RECIPE_NOTE, _RECIPE_PICKUP))

    if not dests:
        st.info("No intake folders found on the NAS yet.")
        return

    dests.sort(key=lambda d: breadcrumb(d.path, nas_root))
    for dest in dests:
        _render_destination(dest, nas_root)
