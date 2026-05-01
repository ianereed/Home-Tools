"""Intake tab — paste/upload files into nas-intake's queue."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import streamlit as st


NAS_INTAKE_DIR = Path.home() / "nas" / "Intake"  # autofs mount on the mini


def render() -> None:
    if not NAS_INTAKE_DIR.exists():
        st.warning(f"NAS intake folder not present at {NAS_INTAKE_DIR}. "
                   "(Either Tailscale autofs is not mounted, or you're running this off the mini.)")
        return

    uploaded = st.file_uploader(
        "Drop a file (image / PDF) — it lands in `~/nas/Intake/` and the watcher picks it up.",
        type=["png", "jpg", "jpeg", "heic", "pdf"],
    )
    if uploaded is not None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        target = NAS_INTAKE_DIR / f"{ts}_{uploaded.name}"
        target.write_bytes(uploaded.getbuffer())
        st.success(f"Wrote {target.name} ({target.stat().st_size:,} bytes). "
                   "The nas-intake watcher will detect + process it within ~5 min.")

    st.divider()
    st.subheader("Recent intake files")
    try:
        entries = sorted(NAS_INTAKE_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    except OSError as exc:
        st.error(f"could not list intake: {exc}")
        return
    if not entries:
        st.caption("(empty)")
        return
    rows = []
    for p in entries:
        rows.append({
            "name": p.name,
            "size (bytes)": p.stat().st_size if p.is_file() else "—",
            "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
    st.dataframe(rows, hide_index=True, use_container_width=True)
