"""Cardiology dashboard page.

Renders the cardiology dataset (LabCorp lipid/statin history overlaid on the
wearable activity record) as a native dashboard page. It reuses the figure and
table builders in `cardiology/build_report.py` so this page and the standalone
HTML report never drift: Plotly figures are shown with st.plotly_chart, and the
report's HTML stat-cards / tables are rendered via st.markdown with the report's
own CSS injected once.

The cardiology module carries PHI (real lipid panels) and is intentionally NOT
committed to git — it is deployed to the homeserver out of band. app.py only
wires this page in when `cardiology/clinical_data.py` is present, and the import
below is lazy, so a checkout without the PHI files simply omits the page.
"""
import os
import sqlite3
import sys

import pandas as pd
import streamlit as st

_HD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CARDIO_DIR = os.path.join(_HD_ROOT, "cardiology")
if _CARDIO_DIR not in sys.path:
    sys.path.insert(0, _CARDIO_DIR)

import build_report as br      # noqa: E402  (cardiology/build_report.py)
import clinical_data as CD     # noqa: E402  (cardiology/clinical_data.py)
from collectors.db import DB_PATH  # noqa: E402


# The report's presentation CSS (cards + clinical tables), scoped so it only
# styles the cardiology page's injected HTML and inherits the dark dashboard bg.
_CARD_CSS = """
<style>
.cardio.summary{background:#10261a;border-left:3px solid #4ade80;padding:12px 16px;
  border-radius:6px;font-size:14px;line-height:1.55;margin:6px 0 14px;}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0;}
.card{background:#161b26;border:1px solid #2a2f3a;border-radius:8px;padding:10px 14px;}
.cardlabel{font-size:11px;color:#8b93a7;text-transform:uppercase;letter-spacing:1px;}
.cardval{font-size:24px;font-weight:650;margin:2px 0;} .cardval.hi{color:#f87171;}
.cardsub{font-size:11px;color:#8b93a7;line-height:1.4;}
.delta{font-size:13px;font-weight:600;} .delta.up{color:#f87171;} .delta.dn{color:#4ade80;}
table.lipid{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0;}
table.lipid th,table.lipid td{border:1px solid #2a2f3a;padding:5px 8px;text-align:left;}
table.lipid th{background:#1a1f2b;} table.lipid td.hi{color:#f87171;font-weight:600;}
table.qtab td,table.qtab th{text-align:right;}
table.qtab td:first-child,table.qtab th:first-child{text-align:left;}
table.qtab tr.empty td{color:#4a5163;}
ul.rm{font-size:13px;line-height:1.6;} .meta{color:#8b93a7;font-size:12px;}
</style>
"""


@st.cache_data(ttl=1800, show_spinner="Building cardiology dataset…")
def _frames():
    """Heavy daily->quarter/week aggregation (scans activity_streams). Cached so
    it only runs on first view / after the TTL, not on every Streamlit rerun."""
    con = sqlite3.connect(DB_PATH)
    try:
        cal = br.build_daily_frame(con)          # opens/closes its own apple sidecar
        q = br.reindex_full(br.summarize(cal, "quarter"), "quarter")
        w = br.reindex_full(br.summarize(cal, "week"), "week")
        w = w[pd.to_datetime(w["bucket_start"]) >= pd.Timestamp(br.WEEKLY_START)] \
            .reset_index(drop=True)
        n_streams = int(br.load(
            con, "SELECT COUNT(DISTINCT activity_id) n FROM activity_streams").iloc[0]["n"])
        data_min = cal["date"].min().date().isoformat()
        data_max = cal["date"].max().date().isoformat()
    finally:
        con.close()
    return q, w, {"data_min": data_min, "data_max": data_max, "acts_with_streams": n_streams}


def _lab_table_html(lip: pd.DataFrame) -> str:
    """Complete per-draw lab panel table (mirrors the report's labs section)."""
    d = lip.copy()
    d["date"] = d["date"].dt.strftime("%Y-%m-%d")
    d["statin"] = d["statin_dose_mg"].apply(lambda x: f"{int(x)} mg" if x else "—")
    cols = [("date", "Date"), ("statin", "Statin"), ("total_chol", "TC"), ("trig", "Trig"),
            ("hdl", "HDL"), ("ldl", "LDL"), ("apob", "ApoB"), ("lpa_nmol_l", "Lp(a)"),
            ("note", "Context")]
    thead = "".join(f"<th>{lbl}</th>" for _, lbl in cols)
    ints = ("total_chol", "trig", "hdl", "ldl", "apob")
    rows = ""
    for _, r in d.iterrows():
        cells = ""
        for col, _lbl in cols:
            v = r[col]
            v = "" if pd.isna(v) else (str(int(v)) if col in ints and pd.notna(v) else v)
            flag = ""
            if col == "ldl" and r["ldl"] and r["ldl"] > 99:
                flag = " class=hi"
            if col == "apob" and pd.notna(r["apob"]) and r["apob"] >= 90:
                flag = " class=hi"
            cells += f"<td{flag}>{v}</td>"
        rows += f"<tr>{cells}</tr>"
    return f"<table class=lipid><thead><tr>{thead}</tr></thead><tbody>{rows}</tbody></table>"


def render_cardiology():
    st.markdown(_CARD_CSS, unsafe_allow_html=True)
    st.markdown("# Cardiology")

    try:
        q, w, meta = _frames()
    except Exception as e:                       # never take the whole dashboard down
        st.error(f"Could not build the cardiology dataset: {e}")
        return

    lip = br.lipids_df()
    end_ts = pd.Timestamp(meta["data_max"])

    st.caption(f"{CD.PATIENT_NAME} · {CD.SEX} · DOB {CD.DOB} · {CD.DESCRIPTOR} · "
               f"activity data through {meta['data_max']}. Lipid/statin data is transcribed "
               "from LabCorp panels; verify against originals before any clinical decision.")
    st.markdown(f'<div class="cardio summary"><b>Clinical picture.</b> {CD.CLINICAL_SUMMARY}</div>',
                unsafe_allow_html=True)
    st.markdown(br.stat_cards_html(lip), unsafe_allow_html=True)

    # ---- headline overlay ------------------------------------------------
    st.markdown("## Lipid response to therapy × exercise")
    st.caption(f"Quarterly exercise intensity (bars, left axis), labeled LDL-C / ApoB draws "
               f"(left axis, mg/dL), {CD.STATIN} dose step (right axis). Pink dotted "
               "verticals = clinical events.")
    st.plotly_chart(br.exec_overlay(q, lip, end_ts), use_container_width=True, key="cardio_exec")

    # ---- quarterly summary table ----------------------------------------
    st.markdown("## Quarterly summary — therapy, labs & lifestyle")
    st.caption("One row per quarter; lab values are the last draw inside the quarter. "
               "Red = above target. **Bold dose ↑** = changed during that quarter. "
               "Grayed rows = no wearable data that quarter.")
    st.markdown(br.quarterly_table_html(q, lip), unsafe_allow_html=True)

    # ---- detailed charts -------------------------------------------------
    with st.expander("Detailed charts — weekly & quarterly trends", expanded=False):
        st.caption("Dashed lines on min/week charts = AHA guidelines: 75 vigorous / "
                   "150 moderate minimum, 300 goal.")
        for i, (label, fig) in enumerate(br.detail_figures(q, w, end_ts)):
            st.markdown(f"### {label}")
            st.plotly_chart(fig, use_container_width=True, key=f"cardio_detail_{i}")

    # ---- complete lab panels --------------------------------------------
    st.markdown("## Complete lab panels by draw date")
    st.markdown(_lab_table_html(lip), unsafe_allow_html=True)
    st.caption("Values mg/dL except Lp(a) (nmol/L). Red = above LabCorp reference. "
               "LDL calculated (NIH).")

    # ---- other risk markers ---------------------------------------------
    st.markdown("## Other cardiovascular-risk markers")
    rm = br.risk_markers_df()
    items = "".join(
        f"<li><b>{r['marker']}</b> — {r['value']} "
        f"<span class=meta>({r['date']}: {r['note']})</span></li>"
        for _, r in rm.iterrows())
    st.markdown(f"<ul class=rm>{items}</ul>", unsafe_allow_html=True)

    # ---- life-context timeline ------------------------------------------
    st.markdown("## Life-context timeline")
    st.caption("Psychosocial / occupational events — kept off the lipid chart (no direct "
               "cholesterol link) but useful context for the activity, resting-HR and sleep trends.")
    st.markdown(br.life_events_html(), unsafe_allow_html=True)

    # ---- methods --------------------------------------------------------
    with st.expander("Methods & caveats"):
        st.markdown(
            f"- **Source coverage varies by era.** Apple full export: steps complete 2016→now; "
            f"official resting HR 2021-06+; workouts 2015+ but HR-zone minutes only 2021+; real "
            f"Apple sleep only from mid-2024 (earlier 'sleep' was a bedtime schedule, excluded). "
            f"Garmin API backfill fills 2020+ resting HR / sleep / HRV / VO₂max.\n"
            f"- **Resting HR** uses Garmin's true daily value where present, then Apple's official "
            f"RestingHeartRate, then a {int(br.RESTING_PCTL * 100)}th-percentile overnight-low "
            f"proxy of intraday samples (dense-sampling days only).\n"
            f"- **'Moderate–vigorous' / 'vigorous' minutes** are HR-zone minutes (Z3–Z5 / Z4–Z5), "
            f"%HRmax bands off an empirical peak of {br.HRMAX} bpm, computed only for activities "
            f"with HR streams ({meta['acts_with_streams']} activities). This is NOT raw workout "
            f"duration — long easy hikes/skis are training volume, not intensity.\n"
            f"- **Activity↔lipid alignment.** Lipid draws span 2020→2026 but quantified activity "
            f"begins {meta['data_min']}; earlier quarters have no activity overlay.\n"
            f"- Lipid/statin/risk-marker values are transcribed from LabCorp reports — verify "
            f"against originals before any clinical decision.")
