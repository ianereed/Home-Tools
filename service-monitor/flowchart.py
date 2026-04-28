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
    white-space: nowrap;
}
.svc-mon-node.ok   { border-left-color: #28a745; }
.svc-mon-node.warn { border-left-color: #fd7e14; }
.svc-mon-node.err  { border-left-color: #dc3545; }
.svc-mon-node.ext  { border-left-color: #6c757d; opacity: 0.75; }
.svc-mon-arrow { color: #4b5563; font-weight: bold; font-size: 0.9rem; }
.svc-mon-shared { background: #1f2937; }
.svc-mon-note { color: #8b95a5; font-size: 0.75rem; }
</style>
"""

_INJECTED = False


def _node(label: str, state: str = "unknown") -> str:
    cls = STATUS_CLASS.get(state, "ext")
    emoji = STATUS_EMOJI.get(state, "⚫")
    return f'<span class="svc-mon-node {cls}">{emoji} {label}</span>'


def _ext(label: str) -> str:
    return _node(label, "unknown")


def _arrow() -> str:
    return '<span class="svc-mon-arrow"> → </span>'


def _lane(header: str, items: list[str], shared: bool = False) -> str:
    cls = "svc-mon-lane svc-mon-shared" if shared else "svc-mon-lane"
    inner = "".join(items)
    hdr = f'<span class="svc-mon-lane-header">{header}</span>' if header else ""
    return f'<div class="{cls}">{hdr}{inner}</div>'


def render_dataflow(status: dict, queues: dict, ollama: dict) -> str:
    """Build HTML swim-lane diagram.

    status: {service_id: {state, pid, last_exit}} from launchd collector
    queues: dict from queues collector
    ollama: dict from ollama collector
    """
    def st_(svc_id: str) -> str:
        return status.get(svc_id, {}).get("state", "unknown")

    ollama_state = "ok" if ollama.get("ok") else "err"
    qd_text = str(queues.get("text_queue_depth", "?")) if queues.get("available") else "?"
    qd_ocr = str(queues.get("ocr_queue_depth", "?")) if queues.get("available") else "?"
    model_count = ollama.get("model_count", 0)

    lanes = [CSS]

    # Event aggregator
    lanes.append(_lane("Event Aggregator", [
        _ext("Gmail / iMsg / Slack / Discord"), _arrow(),
        _node("fetch", st_("evt_fetch")), _arrow(),
        _ext(f"state.json  T:{qd_text}  O:{qd_ocr}"), _arrow(),
        _node("worker", st_("evt_worker")), _arrow(),
        _ext("GCal + Slack #ea"),
    ]))

    # Dispatcher
    lanes.append(_lane("Dispatcher", [
        _ext("Slack #ian-image-intake"), _arrow(),
        _node("dispatcher", st_("disp")), _arrow(),
        _ext("→ event-agg  or  finance-mon/intake/"),
    ]))

    # Health dashboard (split into two sub-rows)
    lanes.append(_lane("Health Dashboard", [
        _ext("iPhone Health"), _arrow(),
        _node("receiver :8095", st_("hd_receiver")),
        '<span class="svc-mon-arrow"> ┐</span>',
        _node("collect 7am", st_("hd_collect")),
        _node("intervals-poll 5m", st_("hd_intervals")),
        _node("staleness", st_("hd_staleness")),
        '<span class="svc-mon-arrow"> ┘</span>',
        _arrow(),
        _ext("health.db"), _arrow(),
        _node("streamlit :8501", st_("hd_streamlit")),
    ]))

    # Finance monitor
    lanes.append(_lane("Finance Monitor", [
        _ext("YNAB API + intake/"), _arrow(),
        _node("watcher 5m", st_("fin_watcher")), _arrow(),
        _ext("finance.db"), _arrow(),
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
