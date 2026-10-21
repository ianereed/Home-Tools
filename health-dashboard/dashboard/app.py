"""Health Dashboard — information-first historical view.

Garmin owns day-to-day training guidance; this dashboard is for holistic,
historical, information-only tracking. Six tabs, one job each:

  Overview    what changed since you last looked, and is the data fresh
  Recovery    sleep, HRV, resting HR, SpO2
  Training    load, volume, HR-zone intensity, steps, the activity log
  Goals       the only place a current value meets a target (PHI-gated)
  Cardiology  the clinical record and long-term history (PHI-gated)
  Nutrition   fuel plan, intake, weight and body composition, diet reference

Every number comes from dashboard.metrics and every target from
dashboard.targets, so tabs can't disagree.
"""

import importlib
import os
import sys
from datetime import datetime

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dashboard import lib                                         # noqa: E402

st.set_page_config(page_title="Health", layout="wide", page_icon="❤️")

st.markdown(f"""
<meta name="theme-color" content="{lib.BG}">
<style>
    html, body, [data-testid="stAppViewContainer"], .stApp {{ background-color: {lib.BG} !important; }}
    /* Drop the default white toolbar/menu so the dark theme is seamless */
    header[data-testid="stHeader"] {{ background: transparent !important; height: 0 !important; }}
    [data-testid="stToolbar"], #MainMenu, footer {{ display: none !important; }}
    .block-container {{ padding-top: 1.2rem; padding-bottom: 1rem; max-width: 1100px; }}
    h2 {{ font-size: 1.05rem !important; font-weight: 650 !important; margin: 1.1rem 0 0.2rem !important; color: #cdd3e0; }}
    h3 {{ font-size: 0.9rem !important; font-weight: 650 !important; margin: 0.6rem 0 0.1rem !important; color: #aab2c5; }}
    .stPlotlyChart {{ margin: -0.3rem 0; }}
    [data-testid="stHorizontalBlock"] {{ gap: 0.6rem; }}
    hr {{ margin: 0.5rem 0 !important; border-color: rgba(255,255,255,0.06) !important; }}
    .stCaption, .stMarkdown small {{ color: {lib.MUTED} !important; }}
    /* page nav: one wrapping row of pills */
    .st-key-nav [data-testid="stButtonGroup"] {{ flex-wrap: wrap; }}
    .st-key-nav button {{ font-size: 0.85rem; }}
</style>
""" + lib.CSS, unsafe_allow_html=True)


# --- since-you-last-looked: capture previous visit once per session --------

if "since_iso" not in st.session_state:
    prev = lib.read_last_seen()
    st.session_state.since_iso = prev.isoformat() if prev else None
    lib.write_last_seen(datetime.now())


# --- navigation ------------------------------------------------------------

# Goals + Cardiology carry PHI (lipid panels, goals, medications) and are
# gated on the out-of-git clinical-data module being present on this host.
HAS_CARDIO = os.path.exists(os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "cardiology", "clinical_data.py"))
PAGES = ["Overview", "Recovery", "Training"] + (["Goals", "Cardiology"] if HAS_CARDIO else []) + ["Nutrition"]

# Old links keep working: ?page=<old tab name> and the old ?p=<index>.
ALIASES = {"sleep": "Recovery", "heart": "Recovery", "heartrv": "Recovery", "wellness": "Training",
           "fitness": "Training", "activity": "Training", "diet": "Nutrition", "fuel": "Nutrition"}
LEGACY_INDEX = (["Overview", "Recovery", "Recovery", "Training", "Training", "Training", "Nutrition"]
                + (["Goals", "Cardiology"] if HAS_CARDIO else []) + ["Nutrition"])


def _slug(name: str) -> str:
    return name.lower().replace(" ", "").replace("&", "")


def _page_from_query() -> str | None:
    if "page" in st.query_params:
        s = _slug(st.query_params["page"])
        for name in PAGES:
            if _slug(name) == s:
                return name
        return ALIASES.get(s) if ALIASES.get(s) in PAGES else None
    if "p" in st.query_params:
        try:
            return LEGACY_INDEX[int(st.query_params["p"])]
        except (ValueError, IndexError):
            return None
    return None


def _keep_page():
    # segmented_control can be clicked off; a page is always selected
    if not st.session_state.get("nav"):
        st.session_state.nav = st.session_state.get("_last_nav", "Overview")
    st.session_state._last_nav = st.session_state.nav


if st.session_state.get("nav") not in PAGES:
    st.session_state.nav = _page_from_query() or "Overview"
st.segmented_control("Page", PAGES, key="nav", label_visibility="collapsed", on_change=_keep_page)
page = st.session_state.nav or "Overview"
if st.query_params.get("page") != _slug(page) or "p" in st.query_params:
    st.query_params.clear()
    st.query_params["page"] = _slug(page)


# --- dispatch --------------------------------------------------------------

def _render(module: str, fn: str, *args):
    """Import a page lazily and keep the rest of the dashboard alive if one fails."""
    try:
        mod = importlib.import_module(f"dashboard.{module}")
    except Exception as e:  # noqa: BLE001
        st.error(f"{page} page unavailable: {e}")
        return
    getattr(mod, fn)(*args)


{
    "Overview": lambda: _render("overview_view", "render_overview", st.session_state.since_iso),
    "Recovery": lambda: _render("recovery_view", "render_recovery"),
    "Training": lambda: _render("training_view", "render_training"),
    "Goals": lambda: _render("goals_view", "render_goals"),
    "Cardiology": lambda: _render("cardiology_view", "render_cardiology"),
    "Nutrition": lambda: _render("nutrition_view", "render_nutrition"),
}[page]()
