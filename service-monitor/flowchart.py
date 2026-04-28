"""Render the data-flow swim lanes as HTML for st.markdown(unsafe_allow_html=True)."""

STATUS_EMOJI = {"ok": "🟢", "warn": "🟡", "err": "🔴", "unknown": "⚫"}
STATUS_CLASS = {"ok": "ok", "warn": "warn", "err": "err", "unknown": "ext"}

CSS = """
<style>
.svc-mon-lane {
    display: flex; align-items: center; gap: 8px; margin: 5px 0;
    padding: 10px 14px; background: #1a1f2e; border-radius: 6px;
    flex-wrap: wrap;
}
.svc-mon-lane-header {
    font-weight: 600; font-size: 0.68rem; color: #8b95a5;
    text-transform: uppercase; letter-spacing: 0.05em;
    min-width: 140px; flex-shrink: 0;
}
.svc-mon-node {
    padding: 4px 10px; border-radius: 4px; background: #262a36;
    border-left: 3px solid #6c757d; font-size: 0.82rem;
    white-space: nowrap; display: inline-flex; flex-direction: column;
    align-items: flex-start;
}
.svc-mon-node.ok   { border-left-color: #28a745; }
.svc-mon-node.warn { border-left-color: #fd7e14; }
.svc-mon-node.err  { border-left-color: #dc3545; }
.svc-mon-node.ext  { border-left-color: #6c757d; opacity: 0.75; }
.svc-mon-arrow { color: #4b5563; font-weight: bold; font-size: 0.9rem; }
.svc-mon-shared { background: #1f2937; }
.svc-mon-note { color: #8b95a5; font-size: 0.75rem; }
.svc-mon-ts { font-size: 0.62rem; color: #6c757d; margin-top: 2px; line-height: 1.1; }
.svc-mon-ts.aging { color: #fd7e14; }
.svc-mon-ts.stale { color: #dc3545; }
</style>
"""


def _age_str(sec: int | None) -> str:
    if sec is None:
        return "—"
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _ts_cls(sec: int | None, aging_sec: int, stale_sec: int) -> str:
    if sec is None or sec < aging_sec:
        return ""
    return "stale" if sec >= stale_sec else "aging"


def _node(label: str, state: str = "unknown", ts: str | None = None, ts_cls: str = "") -> str:
    cls = STATUS_CLASS.get(state, "ext")
    emoji = STATUS_EMOJI.get(state, "⚫")
    ts_html = f'<span class="svc-mon-ts {ts_cls}">{ts}</span>' if ts else ""
    return f'<span class="svc-mon-node {cls}">{emoji} {label}{ts_html}</span>'


def _ext(label: str, ts: str | None = None, ts_cls: str = "") -> str:
    return _node(label, "unknown", ts, ts_cls)


def _arrow() -> str:
    return '<span class="svc-mon-arrow"> → </span>'


def _lane(header: str, items: list[str], shared: bool = False) -> str:
    cls = "svc-mon-lane svc-mon-shared" if shared else "svc-mon-lane"
    inner = "".join(items)
    hdr = f'<span class="svc-mon-lane-header">{header}</span>' if header else ""
    return f'<div class="{cls}">{hdr}{inner}</div>'


def render_dataflow(status: dict, queues: dict, ollama: dict,
                    hdb: dict | None = None, fdb: dict | None = None) -> str:
    """Build HTML swim-lane diagram with freshness timestamps.

    status: {service_id: {state, pid, last_exit}} from launchd collector
    queues: dict from queues collector (includes last_run_ages_sec, worker_updated_age_sec)
    ollama: dict from ollama collector
    hdb: dict from get_health_db()
    fdb: dict from get_finance_db()
    """
    def st_(svc_id: str) -> str:
        return status.get(svc_id, {}).get("state", "unknown")

    ollama_state = "ok" if ollama.get("ok") else "err"
    qd_text = str(queues.get("text_queue_depth", "?")) if queues.get("available") else "?"
    qd_ocr = str(queues.get("ocr_queue_depth", "?")) if queues.get("available") else "?"
    model_count = ollama.get("model_count", 0)

    # Freshness thresholds (seconds)
    FETCH_AGING, FETCH_STALE = 900, 3600       # 15 min, 1h  (fetch every 10 min)
    FILE_AGING, FILE_STALE = 86400, 259200     # 24h, 72h    (event-driven slack_file)
    W_AGING, W_STALE = 1800, 7200             # 30 min, 2h  (KeepAlive worker)
    HDB_AGING, HDB_STALE = 14400, 86400       # 4h, 24h     (iPhone periodic + 7am collect)
    FDB_AGING, FDB_STALE = 600, 7200          # 10 min, 2h  (watcher every 5 min)

    ages = queues.get("last_run_ages_sec", {}) if queues.get("available") else {}
    w_age = queues.get("worker_updated_age_sec") if queues.get("available") else None
    state_mtime = queues.get("mtime_age_sec") if queues.get("available") else None

    def _src(name: str, key: str, aging: int = FETCH_AGING, stale: int = FETCH_STALE) -> str:
        age = ages.get(key)
        return _ext(name, _age_str(age), _ts_cls(age, aging, stale))

    hdb_age = hdb.get("mtime_age_sec") if (hdb and hdb.get("available")) else None
    fdb_age = fdb.get("mtime_age_sec") if (fdb and fdb.get("available")) else None

    lanes = [CSS]

    # Event aggregator — sources expanded individually with per-source fetch age
    lanes.append(_lane("Event Aggregator", [
        _src("Gmail", "gmail"),
        _src("Slack", "slack"),
        _src("iMsg", "imessage"),
        _src("GCal", "gcal"),
        _src("Discord", "discord"),
        _src("slack_file", "slack_file", FILE_AGING, FILE_STALE),
        _arrow(),
        _node("fetch", st_("evt_fetch"),
              _age_str(state_mtime),
              _ts_cls(state_mtime, FETCH_AGING, FETCH_STALE)),
        _arrow(),
        _ext(f"state.json  T:{qd_text}  O:{qd_ocr}"),
        _arrow(),
        _node("worker", st_("evt_worker"),
              _age_str(w_age), _ts_cls(w_age, W_AGING, W_STALE)),
        _arrow(),
        _ext("GCal + Slack #ea"),
    ]))

    # Dispatcher
    lanes.append(_lane("Dispatcher", [
        _ext("Slack #ian-image-intake"), _arrow(),
        _node("dispatcher", st_("disp")), _arrow(),
        _ext("→ event-agg  or  finance-mon/intake/"),
    ]))

    # Health dashboard — health.db shows mtime freshness
    lanes.append(_lane("Health Dashboard", [
        _ext("iPhone Health"), _arrow(),
        _node("receiver :8095", st_("hd_receiver")),
        '<span class="svc-mon-arrow"> ┐</span>',
        _node("collect 7am", st_("hd_collect")),
        _node("intervals-poll 5m", st_("hd_intervals")),
        _node("staleness", st_("hd_staleness")),
        '<span class="svc-mon-arrow"> ┘</span>',
        _arrow(),
        _ext("health.db", _age_str(hdb_age), _ts_cls(hdb_age, HDB_AGING, HDB_STALE)),
        _arrow(),
        _node("streamlit :8501", st_("hd_streamlit")),
    ]))

    # Finance monitor — finance.db shows mtime freshness
    lanes.append(_lane("Finance Monitor", [
        _ext("YNAB API + intake/"), _arrow(),
        _node("watcher 5m", st_("fin_watcher")), _arrow(),
        _ext("finance.db", _age_str(fdb_age), _ts_cls(fdb_age, FDB_AGING, FDB_STALE)),
        _arrow(),
        _node("bot", st_("fin_bot")), _arrow(),
        _ext("Slack DM"),
    ]))

    # Shared infra
    ollama_label = f"Ollama :11434  ({model_count} models)"
    lanes.append(_lane("Shared Infra", [
        _node(ollama_label, ollama_state),
        f'<span class="svc-mon-note">&nbsp; ← used by event-agg / dispatcher / finance-mon</span>',
    ], shared=True))

    # Self
    lanes.append(_lane("This Dashboard", [
        _node("service-monitor :8502", st_("svc_monitor")),
    ], shared=True))

    return "\n".join(lanes)
