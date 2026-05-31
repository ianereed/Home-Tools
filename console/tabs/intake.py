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

import os
import re
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


@st.cache_data(ttl=300, show_spinner=False)
def _discover(root_str: str) -> list[str]:
    """Cached discovery (SMB walks are slow). Returns sorted breadcrumb-order
    string paths; caller maps back to Path."""
    root = Path(root_str)
    intakes = find_intakes(root)
    return sorted((str(p) for p in intakes), key=lambda s: breadcrumb(Path(s), root))


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
                _discover.clear()
        with st.expander("Recent files in this folder"):
            _render_recent(dest.path)


def render() -> None:
    nas_root = _resolve_nas_root()
    if not nas_root.exists() or not nas_root.is_dir():
        st.warning(
            f"NAS not present at `{nas_root}`. "
            "(Either the SMB mount isn't up, or you're running this off the mini.)"
        )
        return

    top = st.columns([1, 4])
    with top[0]:
        if st.button("↻ Refresh folders", use_container_width=True):
            _discover.clear()
            st.rerun()
    with top[1]:
        st.caption(
            f"`intake/` folders under `{nas_root}` (depth ≤ {INTAKE_DEPTH_MAX}) "
            "plus the recipe photo-intake drop zone."
        )

    dests = [
        _Dest(Path(s), _ACCEPT_EXTS, None, _NAS_PICKUP)
        for s in _discover(str(nas_root))
    ]
    recipe_dir = _recipe_photo_dir(nas_root)
    if recipe_dir.exists() and recipe_dir.is_dir():
        dests.append(_Dest(recipe_dir, _PHOTO_EXTS, _RECIPE_NOTE, _RECIPE_PICKUP))

    if not dests:
        st.info("No intake folders found on the NAS yet.")
        return

    dests.sort(key=lambda d: breadcrumb(d.path, nas_root))
    for dest in dests:
        _render_destination(dest, nas_root)
