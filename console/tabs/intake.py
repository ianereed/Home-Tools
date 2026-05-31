"""Intake tab — upload files into each `intake/` folder on the NAS.

`nas-intake` watches every directory named `intake` under the NAS root (depth
<= 4); each one's parent is the *filing scope*. This tab discovers those folders
and gives each its own upload control so the user can push one or more files
straight to the right scope from the dashboard. The watcher then picks them up
unchanged.

Discovery is reimplemented here (not imported from `nas-intake/`): that dir is
hyphenated — not importable as a module — and its `config.py` does a bare
`import config`. The walk is short stdlib; see `nas-intake/discovery.py` for the
canonical copy the watcher uses.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

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
        st.caption(f"Scanning `{nas_root}` for `intake/` folders (depth ≤ {INTAKE_DEPTH_MAX}).")

    intakes = [Path(s) for s in _discover(str(nas_root))]
    if not intakes:
        st.info("No `intake/` folders found on the NAS yet.")
        return

    for intake in intakes:
        crumb = breadcrumb(intake, nas_root)
        with st.container(border=True):
            st.markdown(f"**{crumb}**")
            slug = _slug(intake, nas_root)
            files = st.file_uploader(
                "Select one or more files (image / PDF)",
                type=_ACCEPT_EXTS,
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
                        target = intake / f"{ts}_{f.name}"
                        target.write_bytes(f.getbuffer())
                        written.append((target.name, target.stat().st_size))
                    total = sum(sz for _, sz in written)
                    st.success(
                        f"Uploaded {len(written)} file(s) ({total:,} bytes) to {crumb}. "
                        "The nas-intake watcher will pick them up within ~5 min."
                    )
                    _discover.clear()
            with st.expander("Recent files in this folder"):
                _render_recent(intake)
