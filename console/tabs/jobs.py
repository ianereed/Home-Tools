"""Jobs tab — queue depth, recent firings, kind list, in-flight migrations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st


def _load_migrations_state() -> dict:
    path = Path.home() / "Home-Tools" / "run" / "migrations.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _format_age(iso: str) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def render() -> None:
    st.subheader("Queue")
    try:
        from jobs import huey
        depth = huey.storage.queue_size()
    except Exception as exc:
        st.warning(f"queue unreachable: {exc}")
        depth = None
    if depth is not None:
        col1, col2, col3 = st.columns(3)
        col1.metric("Queue depth", depth)
        col2.metric("DB", str(huey.storage.filename).rsplit("/", 1)[-1])
        col3.metric("Backend", "SqliteHuey")

    st.divider()

    st.subheader("Migrations in flight")
    state = _load_migrations_state()
    in_flight = state.get("in_flight", {})
    if not in_flight:
        st.info("No migrations in flight. (Or migrations.json missing.)")
    else:
        rows = []
        for kind, m in in_flight.items():
            rows.append({
                "kind": kind,
                "soaked (h)": f"{m.get('hours_soaked', 0)} / 72",
                "baseline": m.get("baseline_metric", ""),
                "window": m.get("divergence_window", ""),
                "last fire": _format_age(m.get("last_fire", "")),
                "last check": _format_age(m.get("last_check", "")),
                "started": _format_age(m.get("started_at", "")),
            })
        st.dataframe(rows, hide_index=True, use_container_width=True)

    promoted = state.get("promoted", [])
    rolled_back = state.get("rolled_back", [])
    if promoted or rolled_back:
        st.divider()
        col_p, col_r = st.columns(2)
        with col_p:
            st.metric("Promoted (soaked 72h)", len(promoted))
            for p in promoted[-5:]:
                st.caption(f":white_check_mark: {p.get('kind', '?')} ({_format_age(p.get('at', ''))})")
        with col_r:
            st.metric("Rolled back", len(rolled_back), delta_color="inverse")
            for r in rolled_back[-5:]:
                st.caption(f":x: {r.get('kind', '?')} — {r.get('reason', '?')} ({_format_age(r.get('at', ''))})")

    st.divider()
    st.subheader("Registered kinds")
    try:
        from jobs.cli import _registered_kinds
        kinds = _registered_kinds()
    except Exception as exc:
        st.error(f"could not load kinds: {exc}")
        return
    rows = []
    for name in sorted(kinds):
        fn = kinds[name]
        bl = getattr(fn, "_baseline", None)
        req = getattr(fn, "_requires", None)
        rows.append({
            "name": name,
            "baseline": f"{bl.metric} (window {bl.divergence_window})" if bl else "—",
            "requires": ", ".join(req.items) if req and req.items else "—",
        })
    st.dataframe(rows, hide_index=True, use_container_width=True)
